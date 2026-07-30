"""Jess's Celery task.

**The filename is a hard contract.** ``app.autodiscover_tasks()`` imports
``<label>.tasks`` and nothing else, so a task defined in any other module is registered
in whichever process happens to import it — the web worker, via the backend — and never
in the Celery worker that must run it. The producer then enqueues happily and the
consumer rejects the job as unregistered, with nothing pointing at the cause. That is the
``toto.manta`` bug fixed in 1.21/1.22, and ``tests/test_packaging.py`` now guards it.

Three parts must all be true: this filename, ``"toto.jess"`` in
``registry.TASK_MODULES``, and the label in each host's ``celery_app.py`` autodiscover
list.

**No automatic retries, deliberately.** Nothing in this suite retries, and Jess will not
be the first: a silent exponential backoff hides a misconfigured mail server for hours.
A failed row keeps the provider's error verbatim and waits for a human to press Retry.
"""
from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="toto.jess.tasks.send_mail_message")
def send_mail_message(self, message_id):
    """Deliver one MailMessage. Records the outcome; never raises for a mail failure.

    Model and service imports live inside the function, matching
    ``fileservices/tasks.py:6-7`` — a module-scope model import in a task file runs
    before the app registry is ready in some worker start-up orders.
    """
    from django.utils import timezone

    from . import delivery, vault
    from .models import MailMessage

    row = MailMessage.objects.filter(pk=message_id).first()
    if row is None:
        # Nothing to do and nothing to record — the row was deleted between queueing
        # and pickup. Not an error.
        logger.info("Jess: message %s no longer exists", message_id)
        return {"message_id": message_id, "status": "missing"}

    MailMessage.objects.filter(pk=row.pk).update(
        status=MailMessage.SENDING,
        started_at=timezone.now(),
        attempts=row.attempts + 1,
        task_id=(self.request.id or ""),
        error="",
    )
    row.refresh_from_db()

    try:
        provider = delivery.resolve_provider(row)
        delivery.send_now(row, provider)
    except delivery.NoProvider as exc:
        return _fail(row, str(exc))
    except vault.VaultUnavailable as exc:
        return _fail(row, str(exc))
    except Exception as exc:
        # The provider's own words, kept verbatim. Diagnosing SMTP is exactly when a
        # paraphrase is useless — "Connection refused" and "535 authentication failed"
        # need completely different fixes.
        return _fail(row, f"{type(exc).__name__}: {exc}")

    MailMessage.objects.filter(pk=row.pk).update(
        status=MailMessage.SENT,
        finished_at=timezone.now(),
        provider_label=(provider.label or ""),
        error="",
    )
    logger.info("Jess: sent message %s via %s", row.pk, provider.label)
    return {"message_id": row.pk, "status": MailMessage.SENT}


def _fail(row, error: str):
    """Record a failure on the row and return, rather than raising into Celery.

    Raising would mark the task failed and lose the reason where a human looks for it.
    The soft time limit (5 minutes under the hard one, ``settings.py``) exists so a task
    can do exactly this instead of being SIGKILLed with the row stuck in SENDING.
    """
    from django.utils import timezone

    from .models import MailMessage

    MailMessage.objects.filter(pk=row.pk).update(
        status=MailMessage.FAILED,
        finished_at=timezone.now(),
        error=error,
    )
    logger.warning("Jess: message %s failed: %s", row.pk, error)
    return {"message_id": row.pk, "status": MailMessage.FAILED, "error": error}
