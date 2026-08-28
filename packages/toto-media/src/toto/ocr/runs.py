"""Creating a run, reporting it, and closing it.

Everything that decides a run's shape lives here so the views, the tasks and the
sweep cannot disagree about it. `run_payload` in particular is the one answer to
"how is this going?", used by the status endpoint, the detail page's initial
render and the tests — the idiom the vault's transfers and the antivirus mirror
already share.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from toto.ocr.models import OcrPage, OcrRun, PageStatus, RunStatus


def create_run(*, owner, inspection, language, source_name,
               uploaded=None, vault_file=None) -> OcrRun:
    """One row per submission, with the page rows already frozen.

    The denominator is written here and never recomputed: a progress bar whose
    total moves while you watch it is worse than no bar.
    """
    run = OcrRun(
        owner=owner,
        status=RunStatus.PENDING,
        source_name=source_name[:255],
        source_bytes=getattr(uploaded, "size", 0) or 0,
        source_type=inspection.kind,
        language=language,
        total_pages=inspection.page_count,
        scanned=inspection.scanned,
        scan_note=inspection.scan_note[:255],
    )
    if vault_file is not None:
        run.source_file = vault_file
    run.save()
    if uploaded is not None:
        uploaded.seek(0)
        # Into the media volume, NOT a tempfile: the Celery worker is a
        # different container that mounts this same volume, and a temporary
        # file written by the web process is invisible to it — which would
        # surface as every page failing for no visible reason.
        run.source.save(source_name[:255], uploaded, save=True)

    OcrPage.objects.bulk_create([
        OcrPage(run=run, number=n) for n in range(1, inspection.page_count + 1)
    ])
    return run


def source_path(run: OcrRun) -> str:
    """Where the bytes are, for a subprocess that needs a path."""
    if run.source:
        return run.source.path
    if run.source_file and run.source_file.file:
        return run.source_file.file.path
    raise FileNotFoundError("this run has no source file any more")


def run_payload(run: OcrRun) -> dict:
    """Everything the progress view needs, in one row read plus one count.

    The per-status counts come from a single grouped query — and the trailing
    `.order_by()` is mandatory, not cosmetic: `OcrPage.Meta.ordering` is
    ["number"], and Django folds a model's default ordering into GROUP BY, so
    without it this returns one row per PAGE instead of one per status.
    """
    from django.db.models import Count

    counts = {row["status"]: row["n"] for row in
              OcrPage.objects.filter(run=run).values("status")
              .annotate(n=Count("id")).order_by()}
    return {
        "run_id": run.pk,
        "status": run.status,
        "status_display": run.get_status_display(),
        "finished": run.is_finished,
        "ok": run.status in (RunStatus.SUCCESS, RunStatus.PARTIAL),
        "error": run.error,
        "language": run.language,
        "source_name": run.source_name,
        "total_pages": run.total_pages,
        "pages_done": run.pages_done,
        "pages_failed": run.pages_failed,
        "pages_running": counts.get(PageStatus.RUNNING, 0),
        "pages_waiting": counts.get(PageStatus.WAITING, 0),
        "pages_cancelled": counts.get(PageStatus.CANCELLED, 0),
        "percent": run.percent,
        "page_errors": run.page_errors or [],
        "scanned": run.scanned,
        "scan_note": run.scan_note,
        "text": run.text if run.is_finished else "",
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "now": timezone.now().isoformat(),
    }


def combined_text(run: OcrRun) -> str:
    """The pages, in PAGE order — never in completion order.

    A page that could not be read leaves a marker rather than a hole. A
    300-page result silently missing page 7 is a corrupted document presented
    as a good one, and the reader has no way to know.
    """
    parts = []
    rows = OcrPage.objects.filter(run=run).order_by("number").values_list(
        "number", "status", "text")
    single = (run.total_pages or 0) <= 1
    for number, status, text in rows:
        if status == PageStatus.DONE:
            body = (text or "").strip()
            parts.append(body if single else f"[page {number}]\n{body}")
        elif status == PageStatus.CANCELLED:
            parts.append(_("[page %(n)s was not read — cancelled]")
                         % {"n": number})
        else:
            parts.append(_("[page %(n)s could not be read]") % {"n": number})
    return "\n\n".join(p for p in parts if p).strip()


def finalise(run_id: int) -> None:
    """Close a run: assemble the text, set the outcome, free the source.

    Called by whichever page settles last. Assembling is string work over n
    rows — milliseconds — so it happens inline rather than as another task.
    """
    with transaction.atomic():
        run = OcrRun.objects.select_for_update().get(pk=run_id)
        if run.is_finished:
            return
        text = combined_text(run)
        cancelled = run.pages.filter(status=PageStatus.CANCELLED).exists()
        if run.pages_done == 0:
            status = RunStatus.CANCELLED if cancelled else RunStatus.FAILED
            if not run.error:
                run.error = _("No page could be read.")
        elif run.pages_failed or cancelled:
            status = RunStatus.CANCELLED if cancelled and not run.pages_failed \
                else RunStatus.PARTIAL
        else:
            status = RunStatus.SUCCESS
        run.text = text
        run.status = status
        run.finished_at = timezone.now()
        run.save(update_fields=["text", "status", "finished_at", "error"])

    # Only a wholly successful run gives up its source immediately: anything
    # else may still be retried, and a retry with no source is a dead button.
    if status == RunStatus.SUCCESS:
        run.discard_source()


def settle_page(run_id: int, *, done: bool) -> bool:
    """Count one finished page. True when this was the last one.

    `select_for_update` serialises the read-modify-write so exactly one task
    sees the equality — this is what replaces a Celery chord, which this suite
    has never used and which could not express "finish even though pages
    failed" anyway.
    """
    with transaction.atomic():
        run = OcrRun.objects.select_for_update().get(pk=run_id)
        run.pages_settled += 1
        if done:
            run.pages_done += 1
        else:
            run.pages_failed += 1
        if run.status == RunStatus.PENDING:
            run.status = RunStatus.RUNNING
        run.save(update_fields=["pages_settled", "pages_done", "pages_failed",
                                "status"])
        return run.pages_settled >= (run.total_pages or 0)


def fail_run(run, message: str) -> None:
    """Close a run that could not proceed at all."""
    OcrRun.objects.filter(pk=run.pk).update(
        status=RunStatus.FAILED, error=message[:2000],
        finished_at=timezone.now())
