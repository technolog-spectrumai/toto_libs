"""The nightly sampler and cap reconciler for hosted-git storage.

One task, two halves, on purpose in this order:

1. **Sample.** One paginated admin pass over the whole forge
   (``client.iter_all_repos``), bytes summed per ``owner.login``, mapped to
   ``GiteaAccount.username``, written into ``storage_bytes``. Unmapped owners
   (organisations, hand-made forge accounts) sum into
   ``GiteaForgeSample.unattributed_bytes`` — skipped by the levy, shown to
   staff. The tax sweep then reads the columns, never the forge.

2. **Reconcile the cap.** Effective cap = the account's ``storage_cap_gb``
   override, else ``settings.GITEA_STORAGE_CAP_GB``, else uncapped. Pushes
   cannot be refused — they never pass through Django — so "over the cap"
   means the forge stops accepting NEW repositories
   (``max_repo_creation=0``), restored the moment the holdings drop back
   under. The forge is only called when the state actually changes, so a
   steady forge costs zero admin calls.

Scheduled by the host (``toto.schedules.beat_schedule(gitea=...)``) ahead of
the tax sweep; the levy reading a snapshot means ordering only affects
staleness, never correctness. Errors leave the last sample standing — a
forge that is down must not turn into a failed levy or a spurious block.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

_GB = 2 ** 30
_KIB = 1024


def _effective_cap_bytes(account) -> int | None:
    cap_gb = account.storage_cap_gb
    if cap_gb is None:
        cap_gb = getattr(settings, "GITEA_STORAGE_CAP_GB", None)
    return None if cap_gb in (None, "") else int(cap_gb) * _GB


@shared_task(name="toto.gitea.tasks.gitea_sample_storage")
def gitea_sample_storage() -> dict:
    """Sample the forge, write the snapshot, reconcile every cap."""
    from . import client
    from .models import GiteaAccount, GiteaForgeSample

    by_owner: dict[str, int] = {}
    try:
        for repo in client.iter_all_repos():
            owner = ((repo.get("owner") or {}).get("login") or "").strip()
            raw = int(repo.get("size") or 0) * _KIB   # Gitea reports KiB
            by_owner[owner] = by_owner.get(owner, 0) + raw
    except Exception:                                  # noqa: BLE001
        logger.exception("gitea storage sample failed; keeping the last one")
        return {"sampled": False}

    now = timezone.now()
    accounts = list(GiteaAccount.objects.all())
    known = set()
    blocked = unblocked = 0
    for account in accounts:
        known.add(account.username)
        account.storage_bytes = by_owner.get(account.username, 0)
        account.storage_sampled_at = now

        cap = _effective_cap_bytes(account)
        over = cap is not None and account.storage_bytes > cap
        if over != account.repo_creation_blocked:
            try:
                client.set_max_repo_creation(account.username,
                                             0 if over else -1)
            except Exception:                          # noqa: BLE001
                # The snapshot still lands; the flip retries tomorrow. A
                # forge hiccup must not leave HALF the accounts flipped and
                # the task dead.
                logger.exception("could not %s %s on the forge",
                                 "block" if over else "unblock",
                                 account.username)
            else:
                account.repo_creation_blocked = over
                blocked += int(over)
                unblocked += int(not over)

    GiteaAccount.objects.bulk_update(
        accounts, ["storage_bytes", "storage_sampled_at",
                   "repo_creation_blocked"])

    total = sum(by_owner.values())
    unattributed = sum(raw for owner, raw in by_owner.items()
                       if owner not in known)
    sample = GiteaForgeSample.objects.first()
    if sample is None:
        GiteaForgeSample.objects.create(
            sampled_at=now, total_bytes=total,
            unattributed_bytes=unattributed)
    else:
        sample.sampled_at = now
        sample.total_bytes = total
        sample.unattributed_bytes = unattributed
        sample.save(update_fields=["sampled_at", "total_bytes",
                                   "unattributed_bytes"])

    return {"sampled": True, "accounts": len(accounts),
            "total_bytes": total, "unattributed_bytes": unattributed,
            "blocked": blocked, "unblocked": unblocked}
