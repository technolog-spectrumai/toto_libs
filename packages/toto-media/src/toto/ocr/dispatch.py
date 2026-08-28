"""Queueing a run, and refusing when there is nowhere to queue it.

**Refuse, never inline.** A one-page screenshot read inside the request would be
fine; a 300-page book would park a web worker for twenty minutes. More
decisively, an inline path would be a SECOND code path — does it create page
rows? does it loop? — that only ever executes on hosts nobody tests. So a build
with no worker gets a sentence naming the flag, which is what the vault's
transfers do and for the same reason.
"""

from __future__ import annotations

from django.db.models import F
from django.utils.translation import gettext as _

from toto.celery_utils import celery_available
from toto.ocr.models import OcrPage, OcrRun, PageStatus, RunStatus


class CannotQueue(RuntimeError):
    """No worker. A 503 with an explanation, never a silent degradation."""


def dispatch_run(run: OcrRun) -> None:
    if not celery_available():
        raise CannotQueue(_(
            "Reading a page runs on a background worker, and this server has "
            "none running. Start one and try again."
        ))
    from toto.ocr.tasks import ocr_page

    from toto.ocr.times import page_budget

    budget = page_budget(run.owner)
    OcrRun.objects.filter(pk=run.pk).update(status=RunStatus.RUNNING)
    for number in run.pages.order_by("number").values_list("number", flat=True):
        result = ocr_page.apply_async(
            args=[run.pk, number],
            soft_time_limit=budget, time_limit=budget + 60)
        OcrPage.objects.filter(run=run, number=number).update(task_id=result.id)


def cancel_run(run: OcrRun) -> int:
    """Stop what has not started. Returns how many pages were stopped.

    Cooperative, because `revoke` cannot stop a task that has already begun and
    this codebase has no precedent for terminating one. The load-bearing part is
    the single write below: a task that starts anyway finds its page is no
    longer WAITING and returns without doing work.

    Pages already in flight are allowed to finish. They are seconds from done,
    their text is kept and they are charged, because they ran and delivered —
    and the page says so rather than pretending we can kill them.
    """
    stopped = OcrPage.objects.filter(run=run, status=PageStatus.WAITING).update(
        status=PageStatus.CANCELLED)
    if stopped:
        # F(), not `run.pages_settled + stopped`: a page task can settle between
        # this row being read and being written, and a read-modify-write would
        # throw its increment away — leaving a run that never reaches its own
        # total and so never finalises.
        OcrRun.objects.filter(pk=run.pk).update(
            pages_settled=F("pages_settled") + stopped)
    # Best effort, and correctness never depends on it landing.
    try:
        from celery import current_app

        ids = list(OcrPage.objects.filter(run=run, status=PageStatus.CANCELLED)
                   .exclude(task_id="").values_list("task_id", flat=True))
        if ids:
            current_app.control.revoke(ids)
    except Exception:  # noqa: BLE001
        pass

    run.refresh_from_db()
    if not run.pages.filter(status__in=[PageStatus.WAITING,
                                        PageStatus.RUNNING]).exists():
        from toto.ocr.runs import finalise

        finalise(run.pk)
    return stopped
