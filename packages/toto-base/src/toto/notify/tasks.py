"""toto.notify's one task: the nightly prune (2026-10-04).

The module name is a hard contract: autodiscovery imports ``<label>.tasks``
and nothing else, and "toto.notify" must stay in
``toto.registry.TASK_MODULES``. The beat entry
(``schedules.beat_schedule(notify_prune=...)``) is what turns it on.
"""

import logging

from celery import shared_task

log = logging.getLogger("toto.notify")

TASK_NAME = "toto.notify.tasks.prune_read"


@shared_task(name=TASK_NAME, ignore_result=True, soft_time_limit=300, time_limit=360)
def prune_read() -> dict:
    """Delete the notifications read more than thirty days ago
    (``services.KEEP_READ_DAYS``). Idempotent: a second fire finds nothing."""
    from .services import prune

    deleted = prune()
    if deleted:
        log.info("notify: pruned %s read notifications", deleted)
    return {"deleted": deleted}
