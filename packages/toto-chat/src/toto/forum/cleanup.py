"""Removing what is older than a boundary, permanently (stage 69).

**One rule.** Everything of a channel made before the boundary goes:

* its messages, and with each its image: the row in the vault and the
  bytes (``vault.purge.purge_file``: row first, bytes after the commit);
* its polls, with their options and every answer given in them, open or
  closed;
* the tombstones of what was removed by hand before.

Nothing is spared, and nothing is kept as a copy. What stays: the channel,
its key, its bucket, the community and its members, everything made at or
after the boundary — and **every file in the channel's bucket that no forum
row points at**. The forum owns exactly the vault files its message rows
name (``ForumMessage.attachment``); this module reaches a file only through
such a row and never lists a bucket, so an unrelated file kept there is
not looked at, let alone removed.

**The boundary** is an instant. A scheduled cleanup takes it from the
forum's settings (``ForumSettings.boundary()``: now less the retention age);
an administrator's cleanup takes a chosen age in days, or now itself for
"everything". It is written on the run's record when the run is claimed and
never worked out again.

**Open pages.** When a channel's pass is finished, in the same transaction
that removes its last rows, ``ForumChannel.purged_before`` moves to the
boundary and the channel's event counter (``last_seq``) moves by one. The
feed always answers ``purged_before``, so a page that holds older rows drops
them at its next question. Each chunk holds the channel's row locked, as
posting does (``channels.next_seq``), so a post and a cleanup never
interleave.

**Who starts one, and where it runs.** A cleanup is CLAIMED (``claim``: the
run's record, made RUNNING, and refused while another that would overlap is
live), handed to the worker by ``dispatch.py`` as one run of the "Forum
cleanup" workflow, and FINISHED there (``finish``, called by the workflow's
node in ``predefined_tasks.py``). Nothing is deleted inside a web request.
Chunks of ``DELETE_CHUNK`` messages commit one by one, so a run that is
out of time stops between two of them, closes as "stopped part-way" with
what it removed written down, and the next run continues.

Nothing of a message is logged or recorded here: ids, counts and sizes.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

log = logging.getLogger(__name__)

#: The worker's budget for one dispatch. Well under the host's celery soft
#: limit, so a long night closes its record as "stopped part-way" instead of
#: being killed in the middle of a chunk with nothing written down.
WORKER_DEADLINE_SECONDS = 1200

#: Messages per transaction.
DELETE_CHUNK = 500

#: The ages an administrator may choose, in days (the model's own bounds).
MIN_DAYS, MAX_DAYS = 1, 3650


class CleanupInProgress(Exception):
    """A cleanup that would overlap this one is already running."""


# ---------------------------------------------------------------------------
# What a run would remove
# ---------------------------------------------------------------------------


def boundary_for(days=None, *, now=None):
    """The instant ``days`` days ago; now itself for None ("everything")."""
    now = now or timezone.now()
    return now if days is None else now - timedelta(days=days)


def _doomed(cutoff, channel=None):
    """``(messages, polls)`` made before the cutoff, in one channel or all.
    Tombstones are rows like any other; nothing is exempt."""
    from .models import ChannelPoll, ForumMessage

    messages = ForumMessage.objects.filter(created_at__lt=cutoff)
    polls = ChannelPoll.objects.filter(created_at__lt=cutoff)
    if channel is not None:
        messages, polls = messages.filter(channel=channel), polls.filter(channel=channel)
    return messages, polls


def preview(cutoff, channel=None) -> dict:
    """What a cleanup at ``cutoff`` would remove now. Counts; removes
    nothing."""
    from .models import ForumMessage, PollBallot

    messages, polls = _doomed(cutoff, channel)
    images = messages.filter(attachment__isnull=False).aggregate(
        n=models.Count("pk"), size=models.Sum("attachment_size"))
    everything = ForumMessage.objects.all()
    if channel is not None:
        everything = everything.filter(channel=channel)
    return {
        "boundary": cutoff,
        "messages": messages.count(),
        "images": images["n"] or 0,
        "bytes": images["size"] or 0,
        "polls": polls.count(),
        "ballots": PollBallot.objects.filter(poll__in=polls).count(),
        "total_messages": everything.count(),
    }


# ---------------------------------------------------------------------------
# Claiming
# ---------------------------------------------------------------------------


def in_flight(channel=None) -> bool:
    """Is a cleanup that would overlap this one live? A channel's cleanup
    overlaps its own and a forum-wide one; a forum-wide one overlaps all.

    Asked of the run records, which the stuck-run sweep closes
    (``sweeps.py``), so a worker that died cannot block cleanup for good.
    """
    from .models import ForumCleanupRun, RunStatus

    live = ForumCleanupRun.objects.filter(status__in=(RunStatus.PENDING, RunStatus.RUNNING))
    if channel is not None:
        live = live.filter(models.Q(channel=channel) | models.Q(channel__isnull=True))
    return live.exists()


def claim(*, boundary, retention_days, triggered_by, user=None, channel=None):
    """Make the record of a cleanup, RUNNING, or raise ``CleanupInProgress``.

    The claim is what stops two cleanups overlapping; the worker claims
    nothing itself. Taken under a lock on the settings row, and the
    in-flight question is asked again inside it.
    """
    from .models import ForumCleanupRun, ForumSettings, RunStatus

    with transaction.atomic():
        ForumSettings.objects.select_for_update().filter(pk=ForumSettings.current().pk).first()
        if in_flight(channel):
            raise CleanupInProgress(
                _("A cleanup is already running. Wait for it to finish."))
        return ForumCleanupRun.objects.create(
            status=RunStatus.RUNNING, triggered_by=triggered_by,
            triggered_by_user=user if getattr(user, "pk", None) else None,
            channel=channel, channel_name=channel.name[:255] if channel is not None else "",
            boundary=boundary, retention_days=retention_days)


def scheduled_settings():
    """The settings row if the scheduled cleanup is switched on, else None.
    Reads only: a platform nobody has configured gets no row made for it."""
    from .models import ForumSettings

    row = ForumSettings.objects.filter(pk=1).first()
    return row if row is not None and row.retention_enabled else None


def claim_scheduled():
    """Claim tonight's forum-wide cleanup. None while retention is off (no
    row is touched) or while another cleanup is live."""
    from .models import TriggeredBy

    settings_row = scheduled_settings()
    if settings_row is None:
        return None
    try:
        return claim(boundary=settings_row.boundary(),
                     retention_days=settings_row.retention_days,
                     triggered_by=TriggeredBy.BEAT)
    except CleanupInProgress:
        return None


# ---------------------------------------------------------------------------
# Removing
# ---------------------------------------------------------------------------


def _remove_messages(rows) -> dict:
    """Remove these message rows and the vault files they point at. Call
    inside a transaction. Returns what went."""
    from django.db.models import ProtectedError

    from toto.vault.purge import purge_file

    from .models import ForumMessage

    out = {"messages": len(rows), "files": 0, "bytes": 0, "missing": 0, "held": 0}
    for row in rows:
        vault_file = row.attachment
        if vault_file is None:
            if row.attachment_size is not None:
                # It had an image, and the vault's row had gone before us.
                out["missing"] += 1
            continue
        size = vault_file.file_size_bytes or row.attachment_size or 0
        try:
            with transaction.atomic():
                purge_file(vault_file)
        except ProtectedError:
            # Something else in the platform holds this file: it stays with
            # its owner. The message goes all the same.
            out["held"] += 1
            log.warning("forum cleanup: vault file %s is held elsewhere and was kept",
                        vault_file.pk)
            continue
        out["files"] += 1
        out["bytes"] += size
    ForumMessage.objects.filter(pk__in=[row.pk for row in rows]).delete()
    return out


def _sweep_channel(run, channel_id, cutoff, out_of_time) -> bool:
    """Remove what one channel holds from before ``cutoff``, a chunk a
    transaction. True when the channel is done; False when time ran out."""
    from .models import ForumChannel, ForumMessage, PollBallot

    removed_here = False
    while True:
        if out_of_time():
            return False
        with transaction.atomic():
            # The channel's row, locked: what posting locks to take a number.
            channel = ForumChannel.objects.select_for_update().filter(pk=channel_id).first()
            if channel is None:
                return True     # the community went meanwhile, its channel with it
            messages, polls = _doomed(cutoff, channel)
            rows = list(messages.select_related("attachment")
                        .order_by("created_at", "pk")[:DELETE_CHUNK])
            if rows:
                went = _remove_messages(rows)
                run.messages_deleted += went["messages"]
                run.attachments_deleted += went["files"]
                run.bytes_freed += went["bytes"]
                run.blobs_missing += went["missing"]
                removed_here = True
            else:
                count = polls.count()
                if count:
                    run.ballots_deleted += PollBallot.objects.filter(poll__in=polls).count()
                    run.polls_deleted += count
                    # A bulk delete: the options and the answers go by the
                    # cascade, a final poll's answers among them.
                    polls.delete()
                    removed_here = True
                if (removed_here or channel.purged_before is None
                        or channel.purged_before < cutoff):
                    # The pass is finished: tell the open pages, as an event.
                    channel.last_seq += 1
                    channel.purged_before = max(channel.purged_before or cutoff, cutoff)
                    channel.save(update_fields=["last_seq", "purged_before"])
                if removed_here:
                    run.channels_touched += 1
            run.save(update_fields=["messages_deleted", "attachments_deleted", "bytes_freed",
                                    "blobs_missing", "polls_deleted", "ballots_deleted",
                                    "channels_touched"])
            if not rows:
                return True


def run_cleanup(run, *, deadline_seconds=None):
    """Do the removing for one claimed run: its channel, or every channel."""
    from .models import ForumChannel, RunStatus

    started = time.monotonic()

    def out_of_time() -> bool:
        return deadline_seconds is not None and time.monotonic() - started > deadline_seconds

    if run.channel_id is not None:
        channel_ids = [run.channel_id]
    else:
        channel_ids = list(ForumChannel.objects.order_by("pk").values_list("pk", flat=True))
    for channel_id in channel_ids:
        if not _sweep_channel(run, channel_id, run.boundary, out_of_time):
            return _close(run, RunStatus.PARTIAL, error=str(
                _("Time ran out. The next cleanup continues from here.")))
    return _close(run, RunStatus.SUCCESS)


def _close(run, status, *, error=""):
    run.status = status
    run.error = str(error)[:2000]
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "error", "finished_at"])
    return run


def fail_run(run, reason=""):
    """Close a run that stopped without saying so. The signature the
    platform's stuck-run sweep calls (``sweeps.py``)."""
    from .models import RunStatus

    return _close(run, RunStatus.FAILED,
                  error=str(reason or _("The run stopped without finishing.")))


def finish(runs, *, deadline_seconds=None) -> list[dict]:
    """Do the removing for runs somebody already claimed, in order, within
    ONE deadline for all of them.

    A run that raises is closed FAILED, and so is every run after it that
    never started (a claimed run left RUNNING would block cleanup until the
    sweep came by); then the error is raised again, so the workflow run that
    drove it fails visibly too.
    """
    from .models import RunStatus

    runs = list(runs)
    started = time.monotonic()
    results = []
    for index, run in enumerate(runs):
        if run.channel_id is None and run.channel_name:
            # A CHANNEL's run whose channel was deleted after the claim (the
            # link is SET_NULL). Run as it stands it would read as
            # forum-wide and sweep every channel. The channel's rows went
            # with the channel.
            run.error = str(_("The channel was deleted before this cleanup ran; "
                              "nothing of it was left to remove."))
            run.status = RunStatus.SUCCESS
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "error", "finished_at"])
            results.append({"run": run.pk, "status": run.status, "messages": 0, "bytes": 0})
            continue
        left = None
        if deadline_seconds is not None:
            left = deadline_seconds - (time.monotonic() - started)
        try:
            run_cleanup(run, deadline_seconds=left)
        except Exception as exc:  # noqa: BLE001 - SoftTimeLimitExceeded too
            _close(run, RunStatus.FAILED, error=f"{type(exc).__name__}: {exc}"[:500])
            for rest in runs[index + 1:]:
                fail_run(rest, _("An earlier pass of the same cleanup failed, so this "
                                 "one never started."))
            raise
        results.append({"run": run.pk, "status": run.status,
                        "messages": run.messages_deleted, "bytes": run.bytes_freed})
    return results


def run_scheduled(*, deadline_seconds=None) -> dict:
    """Claim and finish tonight's cleanup in THIS process: what the beat
    task falls back to on a worker whose build has no workflow engine.
    Nothing in a web request calls it."""
    run = claim_scheduled()
    if run is None:
        return {"skipped": "disabled" if scheduled_settings() is None else "in_progress"}
    return {"runs": finish([run], deadline_seconds=deadline_seconds)}


def next_scheduled_run(now=None):
    """When beat would next fire the cleanup, or None if this host has no
    such entry. Read out of ``CELERY_BEAT_SCHEDULE``: beat is a settings
    dict here, so no database row holds a next-run time."""
    from django.conf import settings as django_settings

    entry = (getattr(django_settings, "CELERY_BEAT_SCHEDULE", {}) or {}).get("forum-cleanup")
    if not entry:
        return None
    remaining = getattr(entry.get("schedule"), "remaining_estimate", None)
    if remaining is None:
        return None
    try:
        now = now or timezone.now()
        return now + remaining(now)
    except Exception:  # noqa: BLE001 - a missing "next run" is not an error page
        return None
