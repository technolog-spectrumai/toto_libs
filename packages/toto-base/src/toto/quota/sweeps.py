"""Cross-app closing of runs whose worker died without recording a result.

A hard time limit is a SIGKILL: no ``finally`` runs, the row stays RUNNING,
and the user's spinner never stops. Every run table on the platform had this
hole and none swept it — connectors documents the 2-hour convention but only
stops *blocking* on stale rows, never closes them. This is the one sweeper.

Declarations live in per-app ``toto/<app>/sweeps.py``, collected by
``autodiscover_plugins("sweeps")`` from ``QuotaConfig.ready()`` — the
datalink shape: ``model_label`` is a string resolved lazily via
``apps.get_model`` so a policy never creates an import edge, and a policy for
an app this host does not install is skipped at sweep time. The closer is a
dotted path for the same reason: ``sweeps.py`` modules are imported at
``ready()`` in every process, including the celery-less WSGI tier, and a
string makes it structurally impossible for a declaration to drag in
dispatch/tasks imports at web boot.

Cutoffs are static floors, each ≥ the app's maximum legal runtime (its
celery hard limit at the largest grantable time dial) + a 30-minute margin —
so the sweeper never needs to know about time grants: a row past its floor is
dead by construction.

Deliberately excluded: jess stuck-SENDING. A mail row is evidence, not a job,
and jess's no-retry doctrine is load-bearing — sweep it and you invent
retries. See toto/jess.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("toto.quota.sweeps")

#: Per-tick per-policy runaway guard — a sweeper that finds thousands of rows
#: is cleaning up an incident, and can take several ticks to do it.
MAX_ROWS_PER_TICK = 500


@dataclass(frozen=True)
class StuckRunPolicy:
    model_label: str                    # "texlab.LatexRun" — apps.get_model at sweep time
    active_values: tuple[str, ...]      # statuses meaning "not finished"
    closer: str                         # dotted path -> callable(row, reason)
    cutoff_seconds: int                 # static floor; see module docstring
    status_field: str = "status"
    reference_fields: tuple[str, ...] = ("started_at", "created_at")  # first non-null wins
    task_id_field: str = ""             # "" -> nothing to revoke


_REGISTRY: list[StuckRunPolicy] = []    # declaration order preserved — it is sweep order


class DuplicateSweepPolicy(Exception):
    """Two apps declared a sweep for the same model."""


def register(policy: StuckRunPolicy) -> StuckRunPolicy:
    for existing in _REGISTRY:
        if existing.model_label == policy.model_label:
            if existing == policy:
                return policy
            raise DuplicateSweepPolicy(
                f"{policy.model_label!r} already has a sweep policy.")
    _REGISTRY.append(policy)
    return policy


def all_policies() -> list[StuckRunPolicy]:
    return list(_REGISTRY)


def clear_registry() -> None:
    """Tests only."""
    _REGISTRY.clear()


def run_sweeps(now=None) -> dict[str, int]:
    """Revoke-then-close every stuck row. Returns {model_label: closed}."""
    from datetime import timedelta

    from django.apps import apps
    from django.db.models import Q
    from django.utils import timezone
    from django.utils.module_loading import import_string

    now = now or timezone.now()
    closed: dict[str, int] = {}

    for policy in all_policies():
        try:
            model = apps.get_model(policy.model_label)
        except LookupError:
            log.debug("sweep: %s not installed here", policy.model_label)
            continue
        try:
            close = import_string(policy.closer)
        except ImportError:
            log.error("sweep: closer %r for %s does not import",
                      policy.closer, policy.model_label)
            continue

        floor = now - timedelta(seconds=policy.cutoff_seconds)
        refs = policy.reference_fields
        age_filter = Q(**{f"{refs[0]}__lt": floor})
        for i in range(1, len(refs)):
            null_prior = {f"{refs[j]}__isnull": True for j in range(i)}
            age_filter |= Q(**null_prior, **{f"{refs[i]}__lt": floor})

        rows = (model.objects
                .filter(**{f"{policy.status_field}__in": policy.active_values})
                .filter(age_filter)
                .order_by("pk")[:MAX_ROWS_PER_TICK])

        count = 0
        for row in rows:
            ref = next((getattr(row, f) for f in refs
                        if getattr(row, f, None) is not None), None)
            if ref is None:  # cannot be honest about its age — leave it
                continue
            age_hours = (now - ref).total_seconds() / 3600
            reason = (
                f"Closed by the stuck-run sweeper after {age_hours:.1f}h with "
                "no result. The worker running it was likely killed (deploy, "
                "restart, out-of-memory, or a hard time limit) before it could "
                "record an outcome."
            )
            # Revoke FIRST: a task that is somehow still alive must not
            # resurrect the row after the close. A late write from a revoke
            # that missed is benign and visible in the logs.
            task_id = getattr(row, policy.task_id_field, "") if policy.task_id_field else ""
            if task_id:
                try:
                    from celery import current_app

                    current_app.control.revoke(task_id, terminate=True,
                                               signal="SIGTERM")
                except Exception:  # noqa: BLE001 - no broker is not a reason to keep a dead row
                    pass
            try:
                close(row, reason)
            except Exception:  # noqa: BLE001 - one bad row must not stop the sweep
                log.exception("sweep: closer failed for %s pk=%s",
                              policy.model_label, row.pk)
                continue
            count += 1
            log.info("sweep: closed %s pk=%s (%.1fh old)",
                     policy.model_label, row.pk, age_hours)
        closed[policy.model_label] = count
    return closed
