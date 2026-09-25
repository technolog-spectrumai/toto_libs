"""Temporary rooms, when their time is up.

A temporary room refuses reads and sends from the instant `expires_at`
passes (permissions.can_read asks the clock). This sweep then removes it:
every message and its attachment bytes and every poll — through the cleanup
machinery, recorded as a `ForumCleanupRun` triggered by `expiry` — then the
room key (rooms.shred: cache entry, wrapped row, process copies), then any
socket still open on it (a `room_closed` frame, close 4410), then the room.

Each room is its own unit: one failing room is recorded and the next one
still goes. The run row keeps the room's name after the room is gone.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

log = logging.getLogger(__name__)


def _close_sockets(channel) -> None:
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        layer = get_channel_layer()
        if layer is not None:
            async_to_sync(layer.group_send)(f"forum_{channel.slug}", {"type": "room_closed"})
    except Exception:  # noqa: BLE001 — no channel layer is not a failed expiry
        log.info("forum expiry: no channel layer to tell %s", channel.slug)


def expire_one(channel, *, now=None) -> dict:
    from . import cleanup, rooms
    from .models import ForumCleanupRun, RunStatus, TriggeredBy

    now = now or timezone.now()
    name = channel.name
    run = ForumCleanupRun.objects.create(
        status=RunStatus.RUNNING, triggered_by=TriggeredBy.EXPIRY,
        channel=channel, channel_name=name,
        boundary=now + timedelta(seconds=1), retention_days=0,
    )
    try:
        cleanup.run_cleanup(run)
        rooms.shred(channel)
        _close_sockets(channel)
        channel.delete()
    except Exception as exc:  # noqa: BLE001 — record, and let the next room go
        cleanup.fail_run(run, f"expiry: {exc}"[:500])
        return {"room": name, "ok": False, "error": str(exc)[:200]}
    return {"room": name, "ok": True, "messages": run.messages_deleted}


def expire_due(now=None) -> list[dict]:
    from .models import ForumChannel

    now = now or timezone.now()
    return [expire_one(channel, now=now)
            for channel in ForumChannel.objects.filter(expires_at__lte=now)]
