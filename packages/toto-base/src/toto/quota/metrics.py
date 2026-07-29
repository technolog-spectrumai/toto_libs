"""What each app can meter, declared once.

A metric code used to be written by hand in about six places — the limit check,
the charge, the idempotency key, the rate card, the docs and the tests — with
nothing linking them. They drifted: the same action carried one name for its
limit and another for its charge, and half the seeded rate card named metrics no
code ever charged. This is the one place a metric is declared, and everything
else reads it.

An app declares its metrics in ``<app>/metrics.py``::

    from toto.quota.metrics import Metric, registry

    registry.register(Metric(
        code="texlab.compile",
        label="LaTeX compile",
        app_label="texlab",
        unit="request",
        default_limit=20,
    ))

That module is imported from ``QuotaConfig.ready()``, which runs before
migrations and during ``collectstatic``, so **it must be pure data** — no model
imports, no database, no settings that might not be loaded. Everything a metric
needs to describe itself is a string or a number.

**The code is the whole identity.** The same string names the limit, the charge
and the idempotency key. Anything else re-opens the drift this file exists to
close.

**No prices here.** The registry says what is meterable and in what unit; what
it costs lives in the host's billing app. ``toto.quota.charge`` goes to some
length to keep this app ignorant of money, and a "suggested price" field would
undo that for the sake of one import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterator

from .choices import Period


class DuplicateMetric(Exception):
    """Two apps claimed the same metric code."""


@dataclass(frozen=True)
class Metric:
    """One meterable action.

    ``default_limit`` is a suggestion, not enforcement — ``ingress_quota`` seeds
    a policy row from it, and an admin can change or delete that row afterwards.
    ``None`` means the metric is measured but never capped by default.
    """

    code: str
    label: str
    app_label: str
    unit: str = "request"
    description: str = ""
    default_limit: Decimal | None = None
    period: str = Period.DAILY
    metadata: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.code} — {self.label}"


class MetricRegistry:
    """In-memory, populated at startup. Never touches the database."""

    def __init__(self) -> None:
        self._metrics: dict[str, Metric] = {}

    def register(self, metric: Metric) -> Metric:
        """Add a metric. Raises on a duplicate code.

        Raising rather than overwriting is deliberate: a silent overwrite is how
        two apps end up disagreeing about what a code means, and the failure
        would surface much later as a limit that never matches.
        """
        existing = self._metrics.get(metric.code)
        if existing is not None and existing != metric:
            raise DuplicateMetric(
                f"{metric.code!r} is already registered by {existing.app_label!r}; "
                f"{metric.app_label!r} cannot claim it too."
            )
        self._metrics[metric.code] = metric
        return metric

    def get(self, code: str) -> Metric | None:
        return self._metrics.get(code)

    def all(self) -> list[Metric]:
        return sorted(self._metrics.values(), key=lambda m: (m.app_label, m.code))

    def for_app(self, app_label: str) -> list[Metric]:
        return [m for m in self.all() if m.app_label == app_label]

    def by_app(self) -> dict[str, list[Metric]]:
        """Grouped for a UI that lists metrics under their owning app."""
        grouped: dict[str, list[Metric]] = {}
        for metric in self.all():
            grouped.setdefault(metric.app_label, []).append(metric)
        return grouped

    def codes(self) -> list[str]:
        return [m.code for m in self.all()]

    def installed(self) -> list[Metric]:
        """Only metrics whose app is actually installed on this host.

        The registry is populated by walking installed apps, so this is normally
        everything — but a metric declared for an app that was later switched
        off should not appear in a UI offering to limit it.
        """
        from django.apps import apps

        return [m for m in self.all() if _app_config(m.app_label) is not None]

    def __len__(self) -> int:
        return len(self._metrics)

    def __iter__(self) -> Iterator[Metric]:
        return iter(self.all())

    def __repr__(self) -> str:
        return f"MetricRegistry({len(self._metrics)} metrics)"


#: The singleton every app registers into.
registry = MetricRegistry()


# ---------------------------------------------------------------------------
# Resolving a metric to the table that stores its policies
# ---------------------------------------------------------------------------
# Charging still takes the model explicitly — check_quota(TexlabQuotaPolicy, …).
# That has not changed, and should not: a call site naming its own model cannot
# silently meter into the wrong table. What follows is for *enumeration* only —
# a UI listing every metric has no call site to take the model from, so it
# resolves one from the app label instead.

_policy_cache: dict[str, type] = {}


def _app_config(app_label: str):
    from django.apps import apps

    try:
        return apps.get_app_config(app_label)
    except LookupError:
        return None


def policy_model_for(app_label: str):
    """The concrete AbstractQuotaPolicy subclass owned by this app, or None."""
    if app_label in _policy_cache:
        return _policy_cache[app_label]

    from django.apps import apps

    from .models import AbstractQuotaPolicy

    config = _app_config(app_label)
    if config is None:
        return None

    for model in config.get_models():
        if issubclass(model, AbstractQuotaPolicy):
            _policy_cache[app_label] = model
            return model
    return None


def policy_model_for_metric(code: str):
    """The policy table a metric's limits live in, or None."""
    metric = registry.get(code)
    return policy_model_for(metric.app_label) if metric else None


def clear_caches() -> None:
    """Forget resolved models — for tests that build app registries."""
    _policy_cache.clear()
