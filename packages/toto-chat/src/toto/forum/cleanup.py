"""Removing old conversations, permanently.

Imports no celery. `tasks.py` is a thin wrapper over `run_scheduled()`, so a
host with no worker can call the same code straight from a request — the tax
sweep's doctrine, and the reason "Run cleanup now" works on a box that never
started a worker.

## What it deletes, and what it deliberately does not

Only `ForumMessage` rows older than the boundary, in every channel, and their
attachment bytes. NOT:

* **channels**, even ones left empty — deleting a room is a second,
  differently-shaped destruction nobody asked for;
* **memberships** — removing a `ForumMember` re-syncs the room's vault
  whitelist, and the vault reads an EMPTY `allowed_users` as "every
  authenticated user". A retention sweep must not be able to publish a library;
* **the room's vault library** — those files are the vault's, and Stage 2 left
  them there on purpose;
* **polls** — a poll is a decision record, not a conversation.

## Why the bytes need their own line

Django does not delete a `FileField`'s blob when the row goes. A bulk
`.delete()` would leave every attachment orphaned under
`FORUM_ATTACHMENT_ROOT` — invisible, unreferenced and permanent. So the sweep
collects the names first, deletes the rows, and unlinks after the transaction
commits: row first, blob second, `vault/purge.py`'s order for its stated
reason — *a failed blob delete leaves an invisible orphan, which is strictly
better than a live row pointing at deleted bytes.*

Deletion lives here and NOT in a `post_delete` signal. A signal would make
every future `ForumMessage.delete()` — an admin action, a channel cascade, a
test factory — silently destroy bytes. The consequence is worth writing down:
**an admin delete and a channel cascade still orphan blobs, and this does not
change that.**

## What it does to the leak that already existed

Soft-deleted messages have always kept their bytes forever. The sweep filters
`created_at`, not `deleted_at`, so it drains that leak only as fast as the
messages age: something sent two years ago and deleted yesterday goes on the
next run; something sent and deleted yesterday keeps its bytes until it ages
past the boundary. The page says exactly that and claims no more.
"""

from __future__ import annotations

import logging
import time
from functools import partial

from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

log = logging.getLogger(__name__)

#: Rows per transaction. `vault/mirror.py::_prune_unseen`'s figure. Note a
#: chunk can touch far more rows than this: every reply to a removed message is
#: loaded and updated by the SET_NULL, which is why the deadline is checked
#: between chunks rather than between runs.
DELETE_CHUNK = 500


class CleanupInProgress(Exception):
    """A sweep is already running; a second one would race it."""


def boundary(policy=None, *, now=None):
    from .models import ForumRetentionPolicy

    return (policy or ForumRetentionPolicy.current()).boundary(now=now)


def _doomed(cutoff):
    """Every message older than the cutoff, in every channel.

    Soft-deleted rows included: this is the one pass that removes their bytes,
    and they are older conversations by any reading.
    """
    from .models import ForumMessage

    return ForumMessage.objects.filter(created_at__lt=cutoff)


def preview(policy=None, *, now=None) -> dict:
    """What a run right now would destroy. Counts only; deletes nothing."""
    from .models import ForumChannel, ForumMessage, ForumRetentionPolicy

    policy = policy or ForumRetentionPolicy.current()
    cutoff = policy.boundary(now=now)
    rows = _doomed(cutoff)

    # Trailing order_by(): ForumMessage.Meta.ordering folds into the GROUP BY
    # and would return one row per message instead of one per channel.
    channels = (rows.values("channel_id").annotate(n=models.Count("id"))
                .order_by("channel_id"))
    attachments = rows.exclude(attachment="").exclude(attachment__isnull=True)
    sizes = attachments.aggregate(n=models.Count("id"),
                                  total=models.Sum("attachment_size"))
    return {
        "boundary": cutoff,
        "retention_days": policy.retention_days,
        "messages": rows.count(),
        "attachments": sizes["n"] or 0,
        "bytes": sizes["total"] or 0,
        "channels": len(list(channels)),
        "total_messages": ForumMessage.objects.count(),
        "total_channels": ForumChannel.objects.count(),
    }


def _delete_blobs(names, run_id):
    """Unlink attachment bytes after their rows are gone.

    Through the FIELD's own storage instance — the one thing that wrote them
    is the one thing that deletes them. Calling `forum_attachment_storage()`
    afresh would build a second storage object, and any future divergence
    between the writer's root and the deleter's is a silent orphan farm.
    """
    from .models import ForumCleanupRun, ForumMessage

    storage = ForumMessage._meta.get_field("attachment").storage
    missing = 0
    for name in names:
        try:
            if not storage.exists(name):
                missing += 1
                continue
            storage.delete(name)
        except Exception:  # noqa: BLE001 — one bad unlink must not stop a sweep
            log.exception("forum cleanup: could not delete %r", name)
    if missing and run_id:
        ForumCleanupRun.objects.filter(pk=run_id).update(
            blobs_missing=models.F("blobs_missing") + missing)


def run_cleanup(run, *, deadline_seconds=None) -> "ForumCleanupRun":
    """Do the deleting, chunk by chunk, recording as it goes.

    Every chunk commits, so stopping early is never inconsistent — and the job
    is resumable precisely because the boundary is re-derived from the clock
    on the next run rather than carried around.
    """
    from .models import ForumMessage, RunStatus

    started = time.monotonic()
    cutoff = run.boundary
    touched_channels = set()

    while True:
        if deadline_seconds is not None and (time.monotonic() - started) > deadline_seconds:
            return _close(run, RunStatus.PARTIAL)

        with transaction.atomic():
            chunk = list(_doomed(cutoff).order_by("created_at", "id")
                         .values("id", "channel_id", "attachment",
                                 "attachment_size")[:DELETE_CHUNK])
            if not chunk:
                return _close(run, RunStatus.SUCCESS)

            ids = [row["id"] for row in chunk]
            names = [row["attachment"] for row in chunk if row["attachment"]]
            freed = sum(row["attachment_size"] or 0
                        for row in chunk if row["attachment"])
            touched_channels.update(row["channel_id"] for row in chunk)

            # Counted before the delete, because after it the FK is NULL and
            # there is nothing left to count.
            orphaned = ForumMessage.objects.filter(
                reply_to_id__in=ids).exclude(id__in=ids).count()

            ForumMessage.objects.filter(id__in=ids).delete()

            run.messages_deleted += len(ids)
            run.attachments_deleted += len(names)
            run.bytes_freed += freed
            run.replies_orphaned += orphaned
            run.channels_touched = len(touched_channels)
            run.save(update_fields=["messages_deleted", "attachments_deleted",
                                    "bytes_freed", "replies_orphaned",
                                    "channels_touched"])

            transaction.on_commit(partial(_delete_blobs, names, run.pk))


def _close(run, status, *, error=""):
    from .models import ForumRetentionPolicy

    run.status = status
    run.error = error[:2000]
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "error", "finished_at"])

    policy = ForumRetentionPolicy.current()
    policy.last_run_at = run.finished_at
    policy.last_run_status = status
    policy.last_error = run.error
    policy.save(update_fields=["last_run_at", "last_run_status", "last_error"])
    return run


def fail_run(run, reason=""):
    """Close a run that stopped without saying so.

    The signature the platform's stuck-run sweeper calls (see `sweeps.py`).
    It also clears the policy's live state, which is the half a hand-rolled
    version forgets: a killed worker would otherwise leave the policy reading
    RUNNING forever and every future run refusing to start.
    """
    from .models import RunStatus

    return _close(run, RunStatus.FAILED,
                  error=str(reason or _("The run stopped without finishing.")))


def in_flight() -> bool:
    """Whether a sweep is already going.

    Asked of the RUN rows, not of the policy's `last_run_status`: a killed
    worker leaves the policy saying RUNNING with nothing to reset it, and a
    refusal keyed on that would block every future run forever — the exact
    failure this check exists to prevent. The stuck-run sweeper closes
    abandoned rows, so this answer heals itself.
    """
    from .models import ForumCleanupRun, RunStatus

    return ForumCleanupRun.objects.filter(
        status__in=(RunStatus.PENDING, RunStatus.RUNNING)).exists()


def trigger(*, triggered_by, user=None, policy=None):
    """Claim the sweep and create its run row, or raise CleanupInProgress.

    The claim is taken inside a transaction with `select_for_update()` on the
    policy row — formica's rule, "claim the tick before dispatch so a slow
    cycle cannot double-fire". Note the lock is postgres-only hardening:
    SQLite ignores `select_for_update()` silently, so the refusal must be
    correct without it, which is why the in-flight predicate is re-read inside
    the claim rather than trusted from before it.
    """
    from .models import ForumCleanupRun, ForumRetentionPolicy, RunStatus

    with transaction.atomic():
        policy = (ForumRetentionPolicy.objects.select_for_update()
                  .filter(pk=1).first()) or ForumRetentionPolicy.current()
        if in_flight():
            raise CleanupInProgress(
                _("A cleanup is already running. Wait for it to finish."))
        run = ForumCleanupRun.objects.create(
            status=RunStatus.RUNNING,
            triggered_by=triggered_by,
            triggered_by_user=user,
            boundary=policy.boundary(),
            retention_days=policy.retention_days,
        )
        policy.last_run_at = run.started_at
        policy.last_run_status = RunStatus.RUNNING
        policy.last_error = ""
        policy.save(update_fields=["last_run_at", "last_run_status",
                                   "last_error"])
    return run


def run_scheduled(*, deadline_seconds=None) -> dict:
    """The nightly entry point. Silent and harmless while the dial is off."""
    from .models import ForumRetentionPolicy, RunStatus, TriggeredBy

    policy = ForumRetentionPolicy.current()
    if not policy.enabled:
        return {"skipped": "disabled"}
    try:
        run = trigger(triggered_by=TriggeredBy.BEAT, policy=policy)
    except CleanupInProgress:
        return {"skipped": "in_progress"}
    try:
        run_cleanup(run, deadline_seconds=deadline_seconds)
    except Exception as exc:  # noqa: BLE001 — a failed sweep must close its row
        _close(run, RunStatus.FAILED, error=repr(exc))
        raise
    return {"run": run.pk, "status": run.status,
            "messages": run.messages_deleted, "bytes": run.bytes_freed}


def next_scheduled_run(now=None):
    """When beat would next fire, or None if this host has no schedule.

    Read out of `CELERY_BEAT_SCHEDULE` rather than guessed: beat is a static
    settings dict here (django_celery_beat is not installed), so there is no
    database row holding a next-run time and the page has to compute one.
    """
    from django.conf import settings as django_settings

    entry = (getattr(django_settings, "CELERY_BEAT_SCHEDULE", {}) or {}).get(
        "forum-cleanup")
    if not entry:
        return None
    schedule = entry.get("schedule")
    remaining = getattr(schedule, "remaining_estimate", None)
    if remaining is None:
        return None
    try:
        return (now or timezone.now()) + remaining(timezone.now())
    except Exception:  # noqa: BLE001 — a missing "next run" is not an error page
        return None
