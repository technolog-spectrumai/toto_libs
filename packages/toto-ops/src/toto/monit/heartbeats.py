"""Heartbeats: did every beat entry run as often as its schedule says?

Until 2026-10-01 nothing noticed a dead celery beat. The hourly mana refill,
the faucet payouts, the daily tax levy and the billing never catch up a
missed run — by design, each reads the clock rather than backfilling — so a
beat that stopped on a Friday was a weekend of nobody being paid, found on
Monday by somebody asking.

Three pieces, all here:

- **Run records.** Celery's own signals write a ``TaskRun`` for every run of
  a task the beat schedule names, whoever sent it: a row when it starts
  (``task_prerun``), its outcome when it ends (``task_postrun``) — success
  with a short summary of what it returned, or the error — and a failure the
  worker process itself saw (``task_failure``: a child killed by the hard
  time limit or lost, which never reaches its own postrun). A run nothing
  closed — the worker container stopped mid-run — is closed by the stuck-run
  sweeper after six hours (``toto.monit.sweeps``). The receivers are
  connected in every process (``MonitConfig.ready``) and fire only where
  tasks run. They never raise: a heartbeat that broke the task it records
  would be worse than none.
- **The cadence**, derived from the schedule itself: an interval is its
  length, a crontab the WIDEST gap between two of its firings (``*/7`` is
  seven minutes, not the four across the hour; ``hour="9-17"`` is the sixteen
  hours overnight).
- **The verdict** per entry: not started within ``WARN_AFTER`` × its cadence
  plus ``GRACE_SECONDS`` is WARN, ``FAIL_AFTER`` × is FAIL. The record check
  (``record.check_overdue``) makes one line of it, and says so when NOTHING
  scheduled has started in the most frequent entry's window: that is beat,
  or every worker, gone — not one task.

An entry that has never run is counted from when beat first started with it
(``BeatEntry``, written on the ``beat_init`` signal), else from when this
host began recording (the migration that made these tables) — so a new entry
is not overdue the minute it is added, and one that never runs is still
found. Two entries naming one task share its heartbeat.

What this cannot do: mail anyone that beat is dead. The alert run rides the
same beat (``monit-alert-checks``) and the same worker, so a stopped beat
stops the mail too; the Database page shows it to whoever looks, and the
first alert run after beat comes back reports the entries still overdue.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import record

log = logging.getLogger("toto.monit.heartbeats")

#: Not started within WARN_AFTER × the cadence (plus the grace) is WARN;
#: FAIL_AFTER × is FAIL. Twice, not once: one missed or late firing is a
#: blip, two in a row is a beat that has stopped doing its job.
WARN_AFTER = 2
FAIL_AFTER = 3
#: Room for a deploy's restart and a queue behind a long job — most of all,
#: it keeps an every-minute entry from being called late at the first slow
#: minute.
GRACE_SECONDS = 10 * 60
#: Run records older than this are pruned (MONIT_RUN_RETENTION_DAYS), each
#: task's newest kept whatever its age.
RETENTION_DAYS = 30
SUMMARY_LENGTH = 300
ERROR_LENGTH = 500
#: A summary reads this many items of a list or a dict, this deep.
SUMMARY_ITEMS = 12
SUMMARY_DEPTH = 3
#: The migration that made these tables: when this host began recording.
RECORDING_MIGRATION = ("monit", "0001_initial")

RUNNING, SUCCESS, FAILED, RETRY = "running", "success", "failed", "retry"
#: Celery's states in this table's words; any other is kept, lower-cased.
STATES = {"SUCCESS": SUCCESS, "FAILURE": FAILED, "RETRY": RETRY}

DAY = 86400


# ---------------------------------------------------------------------------
# The schedule and its cadences
# ---------------------------------------------------------------------------


def beat_schedule() -> dict:
    """The host's ``CELERY_BEAT_SCHEDULE``, entries without a task left out."""
    raw = getattr(settings, "CELERY_BEAT_SCHEDULE", None) or {}
    return {name: entry for name, entry in raw.items()
            if isinstance(entry, dict) and entry.get("task")}


def scheduled_tasks() -> set:
    return {entry["task"] for entry in beat_schedule().values()}


def cadence_seconds(schedule) -> float | None:
    """The longest a healthy beat leaves between two runs of an entry, in
    seconds; None when the schedule cannot be read that way."""
    if isinstance(schedule, bool):
        return None
    if isinstance(schedule, (int, float)):
        return float(schedule) if schedule > 0 else None
    if isinstance(schedule, timedelta):
        seconds = schedule.total_seconds()
        return seconds if seconds > 0 else None
    try:
        from celery import schedules
    except ImportError:
        return None
    if isinstance(schedule, schedules.crontab):
        return _crontab_gap(schedule)
    if isinstance(schedule, schedules.solar):
        # Sunrise, sunset and the like: once a day, give or take the season.
        return float(DAY)
    if isinstance(schedule, schedules.schedule):
        return cadence_seconds(schedule.run_every)
    return None


def _crontab_gap(cron) -> float | None:
    """The widest gap between two firings of a crontab, in seconds."""
    times = sorted(hour * 60 + minute for hour in cron.hour for minute in cron.minute)
    if not times:
        return None
    every_day = (len(cron.day_of_week) == 7 and len(cron.day_of_month) == 31
                 and len(cron.month_of_year) == 12)
    days = 1 if every_day else _widest_day_gap(cron)
    if not days:
        return None
    inside = max((later - earlier for earlier, later in zip(times, times[1:])),
                 default=0)
    across = days * 1440 - times[-1] + times[0]
    return float(max(inside, across) * 60)


def _widest_day_gap(cron) -> int | None:
    """The most days between two days a crontab fires on, over eight years
    (leap days and all). Celery fires on a day when its weekday, its day of
    the month and its month all match."""
    day, end = date(2024, 1, 1), date(2032, 1, 1)
    previous, widest = None, 0
    while day < end:
        if (day.month in cron.month_of_year and day.day in cron.day_of_month
                and day.isoweekday() % 7 in cron.day_of_week):
            if previous is not None:
                widest = max(widest, (day - previous).days)
            previous = day
        day += timedelta(days=1)
    return widest or None


def span(seconds) -> str:
    """A length of time in words — "5 minutes", "1 day" — in Django's own
    translated units."""
    from django.utils.timesince import timesince
    from django.utils.translation import ngettext

    if not seconds:
        return "—"
    if seconds < 60:
        count = int(seconds)
        return ngettext("%(count)d second", "%(count)d seconds", count) % {"count": count}
    base = datetime(2001, 1, 1, tzinfo=dt_timezone.utc)
    return timesince(base, base + timedelta(seconds=seconds), depth=2)


# ---------------------------------------------------------------------------
# What a run returned, without its secrets
# ---------------------------------------------------------------------------


def _secret_values() -> list:
    """Every secret setting's value, as a crash report stars them
    (``toto.core.error_reports``); with no request, the settings alone."""
    from toto.core.error_reports import PlatformExceptionReporter

    return PlatformExceptionReporter(None, None, None, None).secret_values()


def _scrub(text: str, known) -> str:
    """One piece of text with the known secret values, a URL's password and
    a secret URL parameter starred. Cut long before, since every secret value
    is looked for in it — far enough out that the final cut never keeps half
    a secret."""
    from toto.core.error_reports import hide_secrets_in

    return hide_secrets_in(text[:4000], known)


def _clip(text: str, length: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= length else text[:length - 1] + "…"


def _compact(value, known, depth=0) -> str:
    from toto.core.error_reports import SECRET_NAMES, SUBSTITUTE

    if value is None:
        return ""
    if isinstance(value, (int, float, Decimal, date)):
        return str(value)
    if isinstance(value, str):
        # Each value on its own: scrubbing the assembled line would read
        # this summary's own "key=value" as URL parameters ("metric_code").
        return _scrub(value, known)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = dataclasses.asdict(value)
    if depth >= SUMMARY_DEPTH:
        return "…"
    if isinstance(value, dict):
        parts = []
        for key, item in list(value.items())[:SUMMARY_ITEMS]:
            text = _compact(item, known, depth + 1)
            if text and SECRET_NAMES.search(str(key)):
                text = SUBSTITUTE
            if text:
                parts.append(f"{key}={text}")
        if len(value) > SUMMARY_ITEMS:
            parts.append("…")
        inner, brackets = ", ".join(parts), "{}"
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        # A list of reports (the levy's, one per rule) reads as reports, not
        # as a bracketed structure: its dicts are spelled as if alone.
        parts = [text for text in (
            _compact(item, known,
                     0 if depth == 0 and isinstance(item, dict) else depth + 1)
            for item in items[:SUMMARY_ITEMS]) if text]
        if len(items) > SUMMARY_ITEMS:
            parts.append("…")
        inner, brackets = "; ".join(parts), "[]"
    else:
        return _scrub(str(value), known)
    if not inner:
        return ""
    return inner if depth == 0 else brackets[0] + inner + brackets[1]


def summarize(value) -> str:
    """What a task returned, in one short line: empty values left out, a
    value under a key that names a secret starred, and so is every secret
    setting's value, a URL's password and a secret URL parameter."""
    from toto.core.error_reports import SUBSTITUTE

    try:
        known = _secret_values()
        text = _compact(value, known)
        for secret in known:  # and once more over the whole line
            text = text.replace(secret, SUBSTITUTE)
        return _clip(text, SUMMARY_LENGTH)
    except Exception:  # noqa: BLE001 - a summary must never cost the record
        log.debug("heartbeats: could not summarise a return value", exc_info=True)
        return ""


def describe(exc) -> str:
    """An exception's type and message, scrubbed like a summary."""
    if exc is None:
        return ""
    name = type(exc).__name__
    try:
        message = str(exc)
        text = f"{name}: {message}" if message else name
        return _clip(_scrub(text, _secret_values()), ERROR_LENGTH)
    except Exception:  # noqa: BLE001 - the type alone, rather than nothing
        return name


# ---------------------------------------------------------------------------
# The signal receivers
# ---------------------------------------------------------------------------

#: When each run in this process started, in case its row could not be
#: written then (a database connection that had just gone away): the end of
#: the run still is, with its start.
_started: dict = {}


def _watched(task) -> bool:
    name = getattr(task, "name", None)
    return bool(name) and name in scheduled_tasks()


def _record_end(task, task_id, values):
    from .models import TaskRun

    started = _started.pop(task_id, None)
    with transaction.atomic():
        if not TaskRun.objects.filter(task_id=task_id).update(**values):
            TaskRun.objects.create(task_id=task_id, task=task.name[:200],
                                   started_at=started or values["finished_at"],
                                   **values)


def on_prerun(sender=None, task_id=None, task=None, **kwargs):
    """A scheduled task starts: its row, running."""
    task = task or sender
    try:
        if not task_id or not _watched(task):
            return
        from .models import TaskRun

        now = timezone.now()
        if len(_started) > 1000:  # runs whose end never came here
            _started.clear()
        _started[task_id] = now
        # A savepoint, so a failed write inside somebody's transaction (an
        # eager run in a test) costs this row and nothing else.
        with transaction.atomic():
            TaskRun.objects.update_or_create(task_id=task_id, defaults={
                "task": task.name[:200], "status": RUNNING, "started_at": now,
                "finished_at": None, "summary": "", "error": ""})
    except Exception:  # noqa: BLE001 - see the module docstring
        log.warning("heartbeats: could not record the start of %s", task_id,
                    exc_info=True)


def on_postrun(sender=None, task_id=None, task=None, retval=None, state=None,
               **kwargs):
    """A scheduled task ended: its outcome, and what it returned."""
    task = task or sender
    try:
        if not task_id or not _watched(task):
            return
        raw = str(state or "").upper()
        status = STATES.get(raw, raw.lower()[:20] or "unknown")
        values = {"status": status, "finished_at": timezone.now()}
        if status == SUCCESS:
            values["summary"] = summarize(retval)
        elif isinstance(retval, BaseException):
            values["error"] = describe(retval)
        _record_end(task, task_id, values)
    except Exception:  # noqa: BLE001 - see the module docstring
        log.warning("heartbeats: could not record the end of %s", task_id,
                    exc_info=True)


def on_failure(sender=None, task_id=None, exception=None, **kwargs):
    """A scheduled task failed. Inside the task this comes just before its
    postrun; from the worker process itself — a child killed by the hard
    time limit, or lost — it is the only word the run gets."""
    try:
        if not task_id or not _watched(sender):
            return
        _record_end(sender, task_id, {"status": FAILED, "finished_at": timezone.now(),
                                      "error": describe(exception)})
    except Exception:  # noqa: BLE001 - see the module docstring
        log.warning("heartbeats: could not record the failure of %s", task_id,
                    exc_info=True)


def close_stuck(run, reason=""):
    """The stuck-run sweeper's closer (``toto.monit.sweeps``): a run whose
    worker went away without a word, failed, with the sweeper's reason."""
    from .models import TaskRun

    TaskRun.objects.filter(pk=run.pk, status=RUNNING).update(
        status=FAILED, finished_at=timezone.now(), error=_clip(reason, ERROR_LENGTH))


def on_beat_init(sender=None, **kwargs):
    """Celery beat starts: note every entry it starts with."""
    try:
        from .models import BeatEntry

        now = timezone.now()
        for name, entry in beat_schedule().items():
            with transaction.atomic():
                row, created = BeatEntry.objects.get_or_create(
                    name=name[:200], defaults={"task": entry["task"][:200],
                                               "first_seen": now, "last_seen": now})
                if not created:
                    BeatEntry.objects.filter(pk=row.pk).update(
                        task=entry["task"][:200], last_seen=now)
    except Exception:  # noqa: BLE001 - see the module docstring
        log.warning("heartbeats: could not note the beat's start", exc_info=True)


def connect():
    """Wire the receivers to Celery's signals; nothing on a host without
    Celery, which has no beat to watch."""
    try:
        from celery import signals
    except ImportError:
        return
    signals.task_prerun.connect(on_prerun, weak=False,
                                dispatch_uid="toto.monit.heartbeats.prerun")
    signals.task_postrun.connect(on_postrun, weak=False,
                                 dispatch_uid="toto.monit.heartbeats.postrun")
    signals.task_failure.connect(on_failure, weak=False,
                                 dispatch_uid="toto.monit.heartbeats.failure")
    signals.beat_init.connect(on_beat_init, weak=False,
                              dispatch_uid="toto.monit.heartbeats.beat")


# ---------------------------------------------------------------------------
# The verdicts
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class EntryState:
    """One beat entry: its cadence, its newest run, and how late it is."""

    name: str
    task: str
    cadence: float | None
    #: The newest TaskRun of the entry's task, or None.
    last: object
    #: What its age counts from: its newest start, or since when it has been
    #: scheduled when that is later (or it never ran).
    since: datetime | None
    age: float | None
    #: record.OK, WARN or FAIL; OFF when it cannot be judged.
    status: str

    @property
    def late(self) -> bool:
        return self.status in (record.WARN, record.FAIL)

    @property
    def cadence_text(self) -> str:
        return span(self.cadence)


def judge(age, cadence) -> str:
    if age is None or not cadence:
        return record.OFF
    if age > FAIL_AFTER * cadence + GRACE_SECONDS:
        return record.FAIL
    if age > WARN_AFTER * cadence + GRACE_SECONDS:
        return record.WARN
    return record.OK


def recording_began():
    """When this host began recording runs — the migration that made the
    tables, applied — or None when that cannot be told."""
    from django.db.migrations.recorder import MigrationRecorder

    app, name = RECORDING_MIGRATION
    return (MigrationRecorder.Migration.objects.filter(app=app, name=name)
            .values_list("applied", flat=True).first())


def entries(now=None) -> list:
    """Every beat entry's state, by name."""
    from .models import BeatEntry, TaskRun

    now = now or timezone.now()
    schedule = beat_schedule()
    if not schedule:
        return []
    first_seen = dict(BeatEntry.objects.filter(name__in=list(schedule))
                      .values_list("name", "first_seen"))
    # One indexed lookup per task rather than a grouping over the table.
    newest = {task: TaskRun.objects.filter(task=task).order_by("-started_at", "-pk").first()
              for task in {entry["task"] for entry in schedule.values()}}
    began, asked = None, False
    states = []
    for name, entry in sorted(schedule.items()):
        task = entry["task"]
        last = newest.get(task)
        marks = [mark for mark in (last.started_at if last else None,
                                   first_seen.get(name)) if mark]
        if marks:
            since = max(marks)
        else:
            if not asked:
                began, asked = recording_began(), True
            since = began
        cadence = cadence_seconds(entry.get("schedule"))
        age = max(0.0, (now - since).total_seconds()) if since else None
        states.append(EntryState(name=name, task=task, cadence=cadence, last=last,
                                 since=since, age=age, status=judge(age, cadence)))
    return states


def quiet_for(states) -> float | None:
    """How long nothing scheduled has started, when that is longer than the
    most frequent entry may wait — beat, or every worker, gone. Else None."""
    judged = [state for state in states if state.status != record.OFF]
    if not judged:
        return None
    quiet = min(state.age for state in judged)
    shortest = min(state.cadence for state in judged)
    return quiet if quiet > WARN_AFTER * shortest + GRACE_SECONDS else None


def beat_started():
    """When celery beat last started, as far as it told us; None if never."""
    from django.db.models import Max

    from .models import BeatEntry

    return BeatEntry.objects.aggregate(last=Max("last_seen"))["last"]


def lateness(state) -> str:
    """One overdue entry, in a few words, for the check's detail."""
    from django.utils.translation import gettext as _

    if state.last is None:
        when = (timezone.localtime(state.since).strftime("%Y-%m-%d %H:%M")
                if state.since else "—")
        return _("%(name)s: never started, scheduled since %(when)s, every "
                 "%(every)s") % {"name": state.name, "when": when,
                                 "every": state.cadence_text}
    return _("%(name)s: last started %(ago)s ago, every %(every)s") % {
        "name": state.name, "ago": span(state.age), "every": state.cadence_text}


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


def retention_days() -> int:
    try:
        return max(1, int(getattr(settings, "MONIT_RUN_RETENTION_DAYS", RETENTION_DAYS)))
    except (TypeError, ValueError):
        return RETENTION_DAYS


def prune(now=None) -> int:
    """Drop runs older than the retention, keeping each task's newest — a
    monthly entry's last run is its heartbeat, however old — and the beat
    entries no longer scheduled that beat has not started with for as long.
    Returns how many runs went."""
    from django.db.models import Max

    from .models import BeatEntry, TaskRun

    now = now or timezone.now()
    cutoff = now - timedelta(days=retention_days())
    keep = list(TaskRun.objects.order_by().values("task")
                .annotate(newest=Max("pk")).values_list("newest", flat=True))
    deleted, _ = (TaskRun.objects.filter(started_at__lt=cutoff)
                  .exclude(pk__in=keep).delete())
    BeatEntry.objects.filter(last_seen__lt=cutoff).exclude(
        name__in=list(beat_schedule())).delete()
    return deleted
