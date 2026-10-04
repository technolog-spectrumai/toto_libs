"""The live socket: ``ws/live/`` (2026-10-04).

One socket per open page, for one thing: the server telling a signed-in
browser that something happened. It is deliberately small.

**Who may open it.** A signed-in SESSION, and nothing else: the user comes
from the session cookie (the host wraps this consumer in channels'
``AuthMiddlewareStack``), there is no ``?token=`` and no bearer subprotocol,
and an anonymous handshake is closed (4401). The host also checks the
handshake's Origin before any of this runs
(``toto.api.ws_origin.same_origin_validator``).

**What it says.** Three messages. The first two have no name in them; the
third — ``{"type": "presence", "event": "in" | "out", "name", "link"}``, a
member of one of this member's communities signed in or out — carries a
display name and a profile address, because it is kept nowhere and there is
no door to ask (``toto.notify.presence``):

* ``{"type": "notification"}`` — the member has news; the page asks the
  bell's door (``notify:api_list``) what it is;
* ``{"type": "folder", "kind": "added" | "changed" | "removed", "file": id,
  "directory": id}`` — a file changed in a folder this page watches; the
  page asks the vault's row door (``vault:file_row``) for that one row.

**What it hears.** Receive-only, but for one message:
``{"type": "watch", "directory": <id>}``. It is honoured only after the
vault's own access check (``toto.vault.live.may_watch``: the bucket's
clearances, pessimistic, then the folder's access list) and answered
``{"type": "watching", "directory": id, "ok": true | false}`` — the same
``false`` for a folder that is not there and one that is not theirs. Anything
else — another type, a malformed or oversized frame, bytes — closes the
socket (4400); so does asking for more than ``MAX_WATCH_REQUESTS``.

**Checked again at every message, not only at the handshake.** A socket
outlives the request that opened it, so before anything is sent the session
is read afresh: still there, still this account, the account still active,
the password not changed since (``signed_in_user``). A session that ended
closes the socket (4401) instead of being told anything more. And every
folder event is put to ``toto.vault.live.may_see`` for this reader before it
is forwarded: watching a folder is not reading every file in it.
"""

from __future__ import annotations

import json
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from toto.core import live

log = logging.getLogger("toto.notify")

#: Close codes (the 4000 range is the application's).
CLOSE_BAD_MESSAGE = 4400
CLOSE_NOT_SIGNED_IN = 4401

#: The longest frame a client may send: a watch message is some forty bytes.
MAX_FRAME = 200
#: Folders one socket may watch, and watch messages it may send at all.
MAX_WATCHED = 100
MAX_WATCH_REQUESTS = 400


def signed_in_user(session_key, user_pk):
    """The account ``session_key`` signs in NOW, if it is still ``user_pk``;
    else ``None``.

    What ``django.contrib.auth.get_user`` checks on every cookie request —
    the backend's ``get_user`` (an inactive account is refused there) and the
    session hash (a password change ends every other session) — read from
    the session store afresh. Read-only: nothing is flushed, cycled or
    re-signed here; the session's own requests do that.
    """
    from importlib import import_module

    from django.conf import settings
    from django.contrib.auth import (
        BACKEND_SESSION_KEY,
        HASH_SESSION_KEY,
        SESSION_KEY,
        get_user_model,
        load_backend,
    )
    from django.utils.crypto import constant_time_compare

    if not session_key or user_pk is None:
        return None
    try:
        session = import_module(settings.SESSION_ENGINE).SessionStore(session_key=session_key)
        raw_user_id = session.get(SESSION_KEY)
        backend_path = session.get(BACKEND_SESSION_KEY)
        if raw_user_id is None or backend_path not in settings.AUTHENTICATION_BACKENDS:
            return None
        user_id = get_user_model()._meta.pk.to_python(raw_user_id)
        if user_id != user_pk:
            return None
        user = load_backend(backend_path).get_user(user_id)
        if user is None or not getattr(user, "is_active", True):
            return None
        if hasattr(user, "get_session_auth_hash"):
            stored = session.get(HASH_SESSION_KEY)
            hashes = [user.get_session_auth_hash(),
                      *getattr(user, "get_session_auth_fallback_hash", lambda: ())()]
            if not stored or not any(constant_time_compare(stored, h) for h in hashes):
                return None
        return user
    except Exception as exc:  # noqa: BLE001 - what cannot be checked is not signed in
        log.warning("live: a session could not be checked (%s)", type(exc).__name__)
        return None


class LiveConsumer(AsyncWebsocketConsumer):
    """See the module docstring."""

    async def connect(self):
        self.user_pk = None
        self.session_key = None
        self.watched = set()
        self.watch_requests = 0
        user = self.scope.get("user")
        session = self.scope.get("session")
        key = getattr(session, "session_key", None)
        if (user is None or not getattr(user, "is_authenticated", False)
                or not getattr(user, "is_active", True) or not key
                or self.channel_layer is None):
            await self.close(code=CLOSE_NOT_SIGNED_IN)
            return
        self.user_pk = user.pk
        self.session_key = key
        await self.channel_layer.group_add(live.user_group(self.user_pk), self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if self.user_pk is None or self.channel_layer is None:
            return
        await self.channel_layer.group_discard(live.user_group(self.user_pk),
                                               self.channel_name)
        for directory_id in self.watched:
            await self.channel_layer.group_discard(self._folder_group(directory_id),
                                                   self.channel_name)

    # -- the one thing a client may say ------------------------------------

    async def receive(self, text_data=None, bytes_data=None):
        directory_id = self._watch_request(text_data)
        if directory_id is None:
            await self.close(code=CLOSE_BAD_MESSAGE)
            return
        self.watch_requests += 1
        if self.watch_requests > MAX_WATCH_REQUESTS:
            await self.close(code=CLOSE_BAD_MESSAGE)
            return
        state = await self._may_watch(directory_id)
        if state is None:
            await self.close(code=CLOSE_NOT_SIGNED_IN)
            return
        allowed = bool(state) and (directory_id in self.watched
                                   or len(self.watched) < MAX_WATCHED)
        if allowed and directory_id not in self.watched:
            await self.channel_layer.group_add(self._folder_group(directory_id),
                                               self.channel_name)
            self.watched.add(directory_id)
        await self._say({"type": "watching", "directory": directory_id, "ok": allowed})

    @staticmethod
    def _watch_request(text_data):
        """The folder a well-formed watch message names, or ``None``."""
        if not isinstance(text_data, str) or len(text_data) > MAX_FRAME:
            return None
        try:
            message = json.loads(text_data)
        except ValueError:
            return None
        if not isinstance(message, dict) or message.get("type") != "watch":
            return None
        directory_id = message.get("directory")
        if isinstance(directory_id, bool) or not isinstance(directory_id, int):
            return None
        if directory_id <= 0 or directory_id > 9223372036854775807:
            return None
        return directory_id

    # -- what the server says ----------------------------------------------

    async def live_notification(self, event):
        if await self._signed_in() is None:
            await self.close(code=CLOSE_NOT_SIGNED_IN)
            return
        await self._say({"type": "notification"})

    async def live_presence(self, event):
        """Somebody of the member's communities signed in or out: a toast,
        kept nowhere (``toto.notify.presence`` chose who hears it)."""
        if await self._signed_in() is None:
            await self.close(code=CLOSE_NOT_SIGNED_IN)
            return
        await self._say({"type": "presence",
                         "event": "out" if event.get("event") == "out" else "in",
                         "name": str(event.get("name") or "")[:150],
                         "link": str(event.get("link") or "")[:300]})

    async def live_folder(self, event):
        try:
            directory_id = int(event.get("directory"))
        except (TypeError, ValueError):
            return
        if directory_id not in self.watched:
            return
        state = await self._may_see(event)
        if state is None:
            await self.close(code=CLOSE_NOT_SIGNED_IN)
            return
        if not state:
            return
        await self._say({"type": "folder", "kind": str(event.get("kind") or ""),
                         "file": event.get("file"), "directory": directory_id})

    async def _say(self, message: dict):
        await self.send(text_data=json.dumps(message))

    # -- the checks, on a thread: they read the database --------------------

    @staticmethod
    def _folder_group(directory_id) -> str:
        from toto.vault.live import folder_group

        return folder_group(directory_id)

    @database_sync_to_async
    def _signed_in(self):
        return signed_in_user(self.session_key, self.user_pk)

    @database_sync_to_async
    def _may_watch(self, directory_id):
        """``None`` — the session ended; else whether the folder may be
        watched. A host without the vault watches nothing."""
        from django.apps import apps

        user = signed_in_user(self.session_key, self.user_pk)
        if user is None:
            return None
        if not apps.is_installed("toto.vault"):
            return False
        from toto.vault.live import may_watch

        try:
            return bool(may_watch(user, directory_id))
        except Exception as exc:  # noqa: BLE001 - what cannot be decided is refused
            log.warning("live: a watch could not be decided (%s)", type(exc).__name__)
            return False

    @database_sync_to_async
    def _may_see(self, event):
        """``None`` — the session ended; else whether this reader may be
        told the event."""
        from django.apps import apps

        user = signed_in_user(self.session_key, self.user_pk)
        if user is None:
            return None
        if not apps.is_installed("toto.vault"):
            return False
        from toto.vault.live import may_see

        try:
            return bool(may_see(user, event))
        except Exception as exc:  # noqa: BLE001 - what cannot be decided is not said
            log.warning("live: an event could not be decided (%s)", type(exc).__name__)
            return False
