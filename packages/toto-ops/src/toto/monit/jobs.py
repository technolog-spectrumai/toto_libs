"""What the background workers have been doing: one list over many tables.

WHY AN AGGREGATOR AND NOT A CELERY MONITOR. The obvious way to answer "show me
queued, running, completed and failed tasks" is to ask Celery. It does not
work here, and the reason is structural rather than a missing setting:
`CELERY_RESULT_BACKEND` is the Redis broker, and a Redis result backend stores
results BY TASK ID. It can answer "how did task abc-123 end" and cannot answer
"which tasks are there" — there is no index to enumerate. `celery inspect`
does enumerate, but only what workers hold RIGHT NOW: a task that finished or
crashed a second ago is gone from it, which is most of what an operator wants
to look at.

The two honest alternatives were `django-celery-results` (a new dependency and
a table written on every task, growing without a sweep) and this: the platform
ALREADY records its jobs. Fifteen models across the suite carry a status,
timestamps and usually an error; nine of them are installed here. They are the
record, and they outlive the broker.

WHAT THIS IS BLIND TO, stated because a monitor that hides its own gaps is
worse than none: a fire-and-forget task that writes no row of its own is
invisible here. So is a task that died before it could write one — which is
exactly the case where the row would have been most useful. `celery_available()`
on the page covers the other half of that question: whether anything is
listening at all. The one exception since 2026-10-01: a task the beat
schedule names gets a row from Celery's own signals whatever it writes
(`monit.TaskRun`, see toto.monit.heartbeats).

WHY EACH SOURCE IS AN ADAPTER. There is no shared base run model and no shared
status vocabulary. Four apps define their own `RunStatus`, and the members do
not agree: OCR has `partial`, workflows have `skipped`, the Lodge has `refused`
and calls the field `state` rather than `status`. Mapping them onto one
canonical set is the whole job of this module, and it is done HERE rather than
by changing fifteen models — those vocabularies are meaningful to their own
apps, and flattening them at the source would lose the distinctions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.utils.translation import gettext_lazy as _

log = logging.getLogger(__name__)

#: The canonical vocabulary this page sorts and filters by. Deliberately the
#: smallest set every source can be mapped onto without inventing a state.
PENDING, RUNNING, DONE, FAILED, OTHER = (
    "pending", "running", "done", "failed", "other")

CANONICAL = (PENDING, RUNNING, DONE, FAILED, OTHER)

#: How each app's own words map onto those five. A member missing from here
#: lands in OTHER rather than being dropped: an unknown state is a thing an
#: operator should see, not a row that vanishes.
_STATUS_MAP = {
    "pending": PENDING, "waiting": PENDING, "queued": PENDING,
    "running": RUNNING,
    # A scheduled task that raised Retry waits for its next attempt
    # (monit.TaskRun, 2026-10-01).
    "retry": PENDING,
    "success": DONE, "done": DONE, "completed": DONE, "ingested": DONE,
    "failed": FAILED,
    # Deliberately NOT "failed": a partial OCR read produced text, a refused
    # archive is the gate working as designed, and a cancelled run is somebody
    # changing their mind. Calling any of them a failure would make the page
    # cry wolf.
    "partial": OTHER, "refused": OTHER, "cancelled": OTHER, "skipped": OTHER,
}


#: The apps' own words for a run's state, as the table's first column shows
#: them (2026-10-02); a word not here is shown untranslated.
RAW_STATUS_LABELS = {
    "pending": _("Pending"), "waiting": _("Waiting"), "queued": _("Queued"),
    "running": _("Running"), "retry": _("Retrying"), "success": _("Succeeded"),
    "done": _("Done"), "completed": _("Completed"), "ingested": _("Ingested"),
    "failed": _("Failed"), "partial": _("Partial"), "refused": _("Refused"),
    "cancelled": _("Cancelled"), "skipped": _("Skipped"), "killed": _("Killed"),
    "lost": _("Lost"),
}


@dataclass(frozen=True)
class JobSource:
    """One run table, described well enough to read generically."""

    key: str
    label: str
    app_label: str          # for apps.is_installed — a STRING, never an import
    model: str              # "app_label.ModelName" for apps.get_model
    status_field: str = "status"
    started_field: str = "started_at"
    finished_field: str = "finished_at"
    created_field: str = "created_at"
    error_fields: tuple = ("error",)
    task_id_field: str = ""
    select_related: tuple = ()
    #: For a table with no status column: a function of the row giving its
    #: raw status, in the words of _STATUS_MAP. `status_field` is then "".
    status_from: object = None


def _faucet_status(run) -> str:
    """A FaucetRun keeps counts, not a status (2026-10-01). Unfinished is
    running; failing everybody it tried is a failure; failing only some is
    partial — one dry reserve among paying faucets, which the map above
    refuses to call a failure."""
    if run.finished_at is None:
        return "running"
    if run.failed:
        return "partial" if (run.paid or run.skipped) else "failed"
    return "done"


SOURCES: tuple = (
    JobSource(key="workflow", label=_("Workflow runs"),
              app_label="toto.workflows", model="workflows.WorkflowRun",
              finished_field="completed_at", error_fields=(),
              select_related=("workflow",)),
    JobSource(key="workflow_node", label=_("Workflow steps"),
              app_label="toto.workflows", model="workflows.WorkflowNodeRun",
              finished_field="completed_at", created_field="",
              task_id_field="celery_task_id", select_related=("node",)),
    JobSource(key="ocr", label=_("Text recognition"),
              app_label="toto.ocr", model="ocr.OcrRun",
              error_fields=("error",)),
    JobSource(key="scan", label=_("Antivirus scans"),
              app_label="toto.antivirus", model="antivirus.ScanRun",
              # A ScanRun has no `started_at` — it is created and finished. The
              # default name was read through `getattr(..., None)`, so it never
              # raised; it simply meant no scan has ever had a duration on this
              # page. Saying "" is the honest version of the same result.
              started_field=""),
    JobSource(key="transfer", label=_("Vault transfers"),
              app_label="toto.vault", model="vault.TransferRun",
              task_id_field="task_id"),
    JobSource(key="mirror", label=_("Bucket refreshes"),
              app_label="toto.vault", model="vault.BucketRefreshRun"),
    JobSource(key="aralia", label=_("PDF renders"),
              app_label="toto.aralia", model="aralia.AraliaRun",
              task_id_field="task_id"),
    JobSource(key="ingestion", label=_("Lodge submissions"),
              app_label="toto.lacedo", model="lacedo.IngestionRun",
              status_field="state", error_fields=("detail",)),
    JobSource(key="git", label=_("Git operations"),
              app_label="toto.repo", model="repo.GitRun",
              # A GitRun keeps its failure in `stderr`; there is no `error`
              # column, so the default read an attribute that is not there and
              # every failed push showed on this page with a blank reason —
              # the one column somebody opens the Jobs page to read.
              error_fields=("stderr",)),
    JobSource(key="forum_cleanup", label=_("Forum cleanups"),
              app_label="toto.forum", model="forum.ForumCleanupRun",
              # NO created_at ON THAT MODEL, and the default named one — so
              # every read of this source raised FieldError, was swallowed by
              # the caller's guard and logged as "could not read
              # forum.ForumCleanupRun". The Jobs page has therefore never shown
              # a forum cleanup. `workflow_node` above already uses "" for the
              # same reason; this is the same fix, found in a test's log noise.
              created_field=""),
    # HEAVY JOBS IN A COMPUTE CAPSULE. A declarative row and nothing else:
    # `app_label` is a string for `apps.is_installed` and `model` a string for
    # `apps.get_model`, so toto-ops gains no import edge on toto-anastasia and
    # a host without capsules simply has no such source.
    #
    # Every field name is the default, which is not luck — `Execution` was
    # written to the same shape as the other run tables. `killed` and `lost`
    # are not in `_STATUS_MAP` and land in OTHER on purpose: a killed job is a
    # deadline or somebody's Cancel, which the map already refuses to call a
    # failure, and an unmapped state is meant to be visible rather than
    # dropped.
    JobSource(key="capsule_job", label=_("Capsule jobs"),
              app_label="toto.anastasia", model="anastasia.Execution",
              select_related=("lease",)),
    # A watched install. Its sentence lives in `detail`, not `error`.
    JobSource(key="capsule_install", label=_("Capsule installs"),
              app_label="toto.anastasia", model="anastasia.InstallRun",
              error_fields=("detail",), select_related=("lease",)),
    # EVERY RUN OF A SCHEDULED TASK (2026-10-01), written by Celery's own
    # signals rather than by the task — so the levy, the billing and the
    # sweeps that keep no table of their own are here too, each with a short
    # summary of what it returned. toto.monit.heartbeats has the rest; the
    # page's schedule table shows each entry's newest.
    JobSource(key="beat", label=_("Scheduled tasks"),
              app_label="toto.monit", model="monit.TaskRun",
              created_field="", task_id_field="task_id"),
    # The hourly faucet payouts and the mana pools' refills, one row per
    # execution (and per pool). Counts and no status column: `_faucet_status`
    # reads one off the counts, and the failures' reasons are in `detail`.
    JobSource(key="faucet", label=_("Faucet runs"),
              app_label="toto.assets", model="assets.FaucetRun",
              status_field="", status_from=_faucet_status, created_field="",
              error_fields=("detail",), select_related=("faucet",)),
)


@dataclass
class JobRow:
    """One job, in this page's vocabulary rather than its app's."""

    source: str
    source_label: str
    pk: object
    subject: str
    status: str
    raw_status: str
    created_at: object = None
    started_at: object = None
    finished_at: object = None
    error: str = ""
    task_id: str = ""
    duration_s: float = None

    @property
    def raw_status_label(self):
        """The app's own word for the state, in the reader's language when
        it is one this page knows; an unknown word is shown as it is."""
        return RAW_STATUS_LABELS.get(self.raw_status, self.raw_status)


def _first_error(obj, fields) -> str:
    for name in fields:
        value = getattr(obj, name, "") or ""
        if value:
            return str(value)[:500]
    return ""


def _rows_for(source: JobSource, limit: int) -> list:
    """The newest `limit` rows of one table, or [] if it cannot be read.

    NEVER RAISES. A monitoring page that 500s because one app it watches is
    mid-migration has failed at the one job it has; a source that cannot be
    read is reported as absent instead.
    """
    from django.apps import apps as django_apps

    if not django_apps.is_installed(source.app_label):
        return []
    try:
        model = django_apps.get_model(source.model)
        order = source.created_field or source.started_field or "pk"
        qs = model.objects.all()
        if source.select_related:
            qs = qs.select_related(*source.select_related)
        rows = list(qs.order_by(f"-{order}")[:limit])
    except Exception:  # noqa: BLE001 — see the docstring
        log.debug("monit.jobs: could not read %s", source.model, exc_info=True)
        return []

    out = []
    for obj in rows:
        if source.status_from is not None:
            raw = str(source.status_from(obj) or "")
        else:
            raw = str(getattr(obj, source.status_field, "") or "")
        created = getattr(obj, source.created_field, None) if source.created_field else None
        started = getattr(obj, source.started_field, None)
        finished = getattr(obj, source.finished_field, None)
        duration = None
        if started and finished:
            duration = round((finished - started).total_seconds(), 1)
        out.append(JobRow(
            source=source.key,
            source_label=source.label,
            pk=obj.pk,
            subject=str(obj)[:200],
            status=_STATUS_MAP.get(raw.lower(), OTHER),
            raw_status=raw,
            created_at=created or started,
            started_at=started,
            finished_at=finished,
            error=_first_error(obj, source.error_fields),
            task_id=str(getattr(obj, source.task_id_field, "") or "")
            if source.task_id_field else "",
            duration_s=duration,
        ))
    return out


def recent_jobs(*, limit_per_source: int = 25, status: str = "",
                source_key: str = "") -> list:
    """Every source's newest rows, merged and sorted newest-first.

    PER-SOURCE limit rather than a global one, and deliberately: a global limit
    filled by whichever app happens to be busiest would hide a failing app
    entirely behind a chatty one.
    """
    rows = []
    for src in SOURCES:
        if source_key and src.key != source_key:
            continue
        # BELT AND BRACES. `_rows_for` already swallows its own errors, and
        # this catches anything that escapes it anyway — a model whose __str__
        # raises, a migration mid-flight, a driver error on one table. One
        # source failing must cost that source's rows and nothing else: the
        # page exists to be readable when something is wrong, so it cannot be
        # the thing that breaks when something is wrong.
        try:
            rows.extend(_rows_for(src, limit_per_source))
        except Exception:  # noqa: BLE001 — see above
            log.warning("monit.jobs: source %s could not be listed",
                        src.key, exc_info=True)
    if status:
        rows = [r for r in rows if r.status == status]
    rows.sort(key=lambda r: (r.created_at is None, r.created_at), reverse=True)
    return rows


def summary(rows) -> dict:
    """Counts per canonical status, for the strip at the top of the page."""
    counts = {name: 0 for name in CANONICAL}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    return counts


def available_sources() -> list:
    """The sources this host actually installs, for the filter dropdown."""
    from django.apps import apps as django_apps

    return [s for s in SOURCES if django_apps.is_installed(s.app_label)]
