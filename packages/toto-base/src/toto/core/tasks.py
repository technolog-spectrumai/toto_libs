"""toto.core's tasks: the nightly housekeeping, and the notices' mail
(2026-10-01).

The module name is a hard contract: autodiscovery imports ``<label>.tasks``
and nothing else, and "toto.core" must stay in ``toto.registry.TASK_MODULES``.
The housekeeping's work is ``housekeeping.run``; this is only its door onto a
worker, put there by the beat (``toto.schedules``, ``housekeeping=True``).
``deliver_notice`` is sent by ``notices.send_notice`` alone, never the beat.
"""

from celery import shared_task

TASK_NAME = "toto.core.tasks.nightly_housekeeping"


@shared_task(name=TASK_NAME, soft_time_limit=1500)
def nightly_housekeeping() -> dict:
    """Expired sessions, the sign-in rows of sessions that are gone, and
    membership applications that lapsed long enough ago — one audit record
    per run, counts only. Idempotent: a second fire finds nothing due."""
    from .housekeeping import run

    return run(source="beat")


NOTICE_TASK_NAME = "toto.core.tasks.deliver_notice"


@shared_task(name=NOTICE_TASK_NAME, bind=True, ignore_result=True, max_retries=None,
             soft_time_limit=120, time_limit=150)
def deliver_notice(self, message: dict) -> dict:
    """Send one rendered notice (``toto.core.notices``), trying again on
    failure: ``notices.TRIES`` tries in all, ``notices.RETRY_DELAYS`` apart.

    The bound is kept here, not by Celery (``max_retries=None``): Celery's
    own "max retries exceeded" error prints the task's arguments — the
    address and the mail — into the worker's log. The retry carries the
    kind and the error class only, and so does what this returns, which
    the worker logs too.
    """
    from . import notices

    kind = str(message.get("kind", ""))
    tries = self.request.retries + 1
    final = tries >= notices.TRIES
    error = notices.deliver(message, tries=tries, final=final)
    if not error:
        return {"kind": kind, "status": "sent", "tries": tries}
    if final:
        return {"kind": kind, "status": "failed", "tries": tries}
    raise self.retry(countdown=notices.RETRY_DELAYS[tries - 1],
                     exc=notices.NotDelivered(f"{kind}: {error}"))
