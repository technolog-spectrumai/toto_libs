"""The live socket's sending half (2026-10-04).

One WebSocket per page (``ws/live/``, ``toto.notify.consumers.LiveConsumer``)
carries what the server tells a signed-in browser as it happens: that its
member has a new notification, that a file changed in a folder the page
watches. This module is how the rest of the library says so — through the
channel layer, to a GROUP — and it is written so that nothing depends on it:

* **No ``channels`` import at module load.** A host without the socket layer
  imports this freely; ``layer()`` answers ``None`` there and every
  ``publish`` is a no-op.
* **``publish`` never raises.** A Redis that is down costs the push, never
  the change that caused it: the row is in the database, and the page's own
  fallback (the bell asks every minute) finds it.
* **After the commit.** Nothing is announced that rolled back; outside a
  transaction it goes at once.
* **No names on the wire.** A message says THAT something happened and which
  ids it concerns; what it is called is fetched by the page through a door
  that checks the reader (``notify:api_list``, ``vault:file_row``). The one
  exception is presence (``toto.notify.presence``): a sign-in or sign-out is
  kept nowhere, so there is no door to ask, and the message carries the
  person's display name and profile address — to the sockets of the members
  of their own communities and to nobody else.

Groups: ``user.<pk>`` — every socket of one account — and whatever an app
names for itself (the vault's ``folder.<directory pk>``,
``toto.vault.live``). A message's ``type`` names the consumer's handler, as
channels has it (``live.notification`` → ``live_notification``).
"""

from __future__ import annotations

import logging
from functools import partial

log = logging.getLogger("toto.core.live")

#: The handler names a message may carry (``LiveConsumer``'s methods).
NOTIFICATION = "live.notification"
FOLDER = "live.folder"
PRESENCE = "live.presence"

_warned = False


def layer():
    """The host's default channel layer, or ``None``: no ``channels``
    installed, or no ``CHANNEL_LAYERS`` set."""
    try:
        from channels.layers import get_channel_layer
    except ImportError:
        return None
    try:
        return get_channel_layer()
    except Exception as exc:  # noqa: BLE001 - a broken layer is no layer
        _complain(exc)
        return None


def available() -> bool:
    """Does this host push at all? What a page asks before it opens a socket."""
    return layer() is not None


def user_group(user_pk) -> str:
    """Every socket of one account."""
    return f"user.{int(user_pk)}"


def publish(group: str, message: dict) -> None:
    """Send ``message`` to every socket in ``group``, once the current
    transaction commits. Never raises; a host without a channel layer does
    nothing."""
    if layer() is None:
        return
    try:
        from django.db import transaction

        transaction.on_commit(partial(_send, group, dict(message)))
    except Exception as exc:  # noqa: BLE001 - never in the way of the change
        _complain(exc)


def _send(group: str, message: dict) -> None:
    channel_layer = layer()
    if channel_layer is None:
        return
    try:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            # Called on an event loop's own thread (an async view, a test):
            # async_to_sync refuses there, so the send is left to the loop.
            task = loop.create_task(channel_layer.group_send(group, message))
            task.add_done_callback(_done)
            return
        from asgiref.sync import async_to_sync

        async_to_sync(channel_layer.group_send)(group, message)
    except Exception as exc:  # noqa: BLE001 - the push is lost, the change is not
        _complain(exc)


def _done(task) -> None:
    if not task.cancelled() and task.exception() is not None:
        _complain(task.exception())


def _complain(exc) -> None:
    """Once loudly, then quietly: a broker that is down fails every push,
    and a warning per file would bury the log."""
    global _warned
    level = logging.DEBUG if _warned else logging.WARNING
    _warned = True
    log.log(level, "live: a push was not sent (%s)", type(exc).__name__)
