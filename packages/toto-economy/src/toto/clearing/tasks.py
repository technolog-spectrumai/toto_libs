"""Celery tasks for the clearing bridge.

The module name is a hard contract: autodiscovery imports ``<label>.tasks`` and
nothing else, and the label must also appear in ``toto.registry.TASK_MODULES``
and the host's celery app. There are no automatic retries — a delivery that
fails keeps the peer's error verbatim and waits for the redrive sweep, then for
a human (the mail-outbox discipline). Every task is a plain function underneath,
so a host with no worker can call the service directly.
"""

from __future__ import annotations

import logging

from celery import shared_task

from .services import holds as holds_service
from .services import transport as transport_service

logger = logging.getLogger(__name__)


@shared_task(name="toto.clearing.tasks.dispatch_outbox")
def dispatch_outbox() -> int:
    sent = transport_service.dispatch_queued()
    if sent:
        logger.info("clearing: delivered %s message(s)", sent)
    return sent


@shared_task(name="toto.clearing.tasks.redrive_failed")
def redrive_failed() -> int:
    requeued = transport_service.redrive()
    if requeued:
        logger.info("clearing: re-queued %s failed message(s)", requeued)
    return requeued


@shared_task(name="toto.clearing.tasks.expire_holds")
def expire_holds() -> int:
    """Void holds past their deadline.

    Safe by construction for the in-doubt case: a sender's hold outlives the
    receiver's deadline by a fixed margin, so the receiver has always decided
    before this can fire on it.
    """
    expired = holds_service.expire_stale_holds()
    if expired:
        logger.info("clearing: expired %s hold(s)", expired)
    return expired
