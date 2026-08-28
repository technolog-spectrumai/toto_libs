"""One Celery task per page, and the nightly sweep.

**Nothing at module level imports a model.** On a host where this package is
importable but `toto.ocr` is not in INSTALLED_APPS, importing a models module
raises `RuntimeError: Model class ... doesn't declare an explicit app_label` —
and Celery's autodiscovery swallows only ImportError, so that surfaces as a
broken app rather than a skipped one. It is the exact failure `toto.gitea` hit,
and `toto.forum.tasks` is the shape that avoids it.

`toto.ocr` must therefore also be named in `toto.registry.TASK_MODULES`, or beat
enqueues the sweep forever and the worker answers KeyError to every one.
"""

from __future__ import annotations

import logging
import tempfile

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2,
             soft_time_limit=240, time_limit=300)
def ocr_page(self, run_id: int, number: int):
    """Read one page, in its own process, and account for it exactly once."""
    from django.db import transaction
    from django.utils import timezone

    from toto.ocr import engine, runs
    from toto.ocr.models import OcrPage, OcrRun, PageStatus, RunStatus

    # --- claim the page, or stand down -----------------------------------
    with transaction.atomic():
        try:
            page = OcrPage.objects.select_for_update().get(run_id=run_id,
                                                           number=number)
        except OcrPage.DoesNotExist:
            return "gone"
        run = OcrRun.objects.get(pk=run_id)
        # The cancellation checkpoint. `cancel_run` flips every WAITING page in
        # one write, so a task that started anyway finds this and does nothing.
        if run.status in (RunStatus.CANCELLED, RunStatus.FAILED):
            return "cancelled"
        if page.status != PageStatus.WAITING:
            return "already-settled"
        page.status = PageStatus.RUNNING
        page.attempts += 1
        page.task_id = getattr(self.request, "id", "") or ""
        page.started_at = timezone.now()
        page.save(update_fields=["status", "attempts", "task_id", "started_at"])
        if run.status == RunStatus.PENDING:
            OcrRun.objects.filter(pk=run_id, status=RunStatus.PENDING).update(
                status=RunStatus.RUNNING, started_at=timezone.now())

    # --- do the work ------------------------------------------------------
    text, failure = "", ""
    try:
        source = runs.source_path(run, number)
        if run.source_type == "pdf":
            # A temp dir holding exactly ONE rendered page. Rendering the whole
            # book up front would be ~450 MB of scratch; `-f N -l N` costs about
            # one page's work regardless of how long the document is.
            with tempfile.TemporaryDirectory(prefix="ocr-page-") as tmp:
                image = engine.render_pdf_page(source, number, tmp)
                text = engine.read_image(image, run.language)
        else:
            text = engine.read_image(source, run.language)
    except Exception as exc:  # noqa: BLE001 — every failure is THIS page's
        failure = str(exc)[:2000] or exc.__class__.__name__
        # A transient failure gets one more go; a file that is not a page will
        # fail identically forever, so retrying it just burns the queue.
        if self.request.retries < self.max_retries and _looks_transient(exc):
            OcrPage.objects.filter(pk=page.pk).update(
                status=PageStatus.WAITING, error=failure)
            raise self.retry(exc=exc, countdown=30)

    # --- record it, once --------------------------------------------------
    with transaction.atomic():
        fields = {"status": PageStatus.DONE if not failure else PageStatus.FAILED,
                  "text": text, "error": failure,
                  "finished_at": timezone.now()}
        # The WHERE clause is the idempotency guard: a redelivered task whose
        # page is no longer RUNNING updates nothing and settles nothing.
        claimed = OcrPage.objects.filter(pk=page.pk,
                                         status=PageStatus.RUNNING).update(**fields)
        if not claimed:
            return "already-settled"
        if failure:
            fresh = OcrRun.objects.select_for_update().get(pk=run_id)
            fresh.add_page_error(number, failure, page.attempts)
            fresh.save(update_fields=["page_errors"])

    if not failure:
        _meter_page(run, number)

    if runs.settle_page(run_id, done=not failure):
        runs.finalise(run_id)
    return "ok" if not failure else "failed"


def _looks_transient(exc) -> bool:
    """Worth one more attempt, or hopeless?

    A timeout or a missing file may be a busy worker or a slow volume; a page
    that is not a page will fail the same way every time, and retrying it only
    delays every other job on the one queue this platform has.
    """
    import subprocess

    return isinstance(exc, (subprocess.TimeoutExpired, OSError))


def _meter_page(run, number: int) -> None:
    """Count and charge one page, after it has actually delivered.

    Metering at DELIVERY rather than at submission is what makes failed pages
    free, and it is why this app needs no refund path at all: manta refunds
    because it charges up front, and the rule there — work that ran and failed
    keeps its charge, work never delivered does not — comes out the same way
    here without any ledger surgery.
    """
    try:
        from toto.quota import record_usage
        from toto.quota.charge import charge, price_for

        from toto.ocr.models import OcrUsageEvent

        source = {"source_type": "ocr.OcrRun", "source_id": str(run.pk)}
        wrote = record_usage(
            OcrUsageEvent, "ocr.page", 1, run.owner, unit="page",
            idempotency_key=f"ocr.page:{run.pk}:{number}", **source)
        # The charge only ever follows a usage event that was actually written,
        # so a redelivered task cannot bill twice. manta's idiom.
        if wrote is not None:
            charge(run.owner, price_for(run.owner, "ocr"), "ocr.page", 1,
                   unit="page", **source)
    except Exception:  # noqa: BLE001 — metering must never lose a read page
        logger.exception("ocr: could not meter page %s of run %s", number, run.pk)


@shared_task(soft_time_limit=1500, time_limit=1740)
def ocr_cleanup():
    """Remove finished runs past their retention, and their bytes with them."""
    from toto.ocr.models import OcrRun, OcrSettings, TERMINAL

    boundary = OcrSettings.boundary()
    if boundary is None:
        return "disabled"

    stale = (OcrRun.objects.filter(status__in=TERMINAL,
                                   finished_at__lt=boundary)
             .order_by("finished_at")[:500])
    removed = 0
    for run in list(stale):
        # The blob first: deleting the row does NOT delete the file, and a row
        # deleted with its bytes still on disk is an orphan nothing collects.
        run.discard_source()
        run.delete()
        removed += 1
    return f"removed {removed}"
