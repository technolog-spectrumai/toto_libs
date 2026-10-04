"""The bell's three doors and the long-poll door (2026-10-04).

Each acts on ``request.user`` and on nobody else — no door takes an account's
id, and a notification that is not the caller's answers 404, the same as one
that does not exist. They are the session's doors: a signed-in cookie, the
CSRF token on the two writes (they are NOT ``csrf_exempt``), and the
Fetch-Metadata guard in front of the writes and of the long poll
(``toto.api.fetch_metadata``), so a request a browser sent from another site
is refused before anything is read. JSON in and out, never cached.

The list is drawn here, at reading time: in the reader's language and time
zone, and less what they may no longer be told (``services.listing``).

**The long poll** — ``api_wait``, ``GET notify/api/wait/?cursor=…&folders=1,2``
— is how an open page learns that something changed without asking again
and again, and without a WebSocket:

* it answers AT ONCE when the cursor is behind (or there is none);
* otherwise it holds the request until the member's bell or one of the
  folders changes, or ``wait.HOLD_SECONDS`` pass, and then answers
  ``{"cursor", "notifications": true|false, "folders": [ids], "files":
  {folder id: {file id: version tag}}}`` — ids only, never a name or a
  sentence. The page then asks the doors that check the reader:
  ``notify:api_list`` for the bell, ``vault:file_row`` for a row.
* a folder is reported only after the vault's own access check, made again
  at every poll and after every wake (``wait.watched``); one the reader may
  not watch is not listened to at all.

**What a held request costs.** The view is async: while it waits it is a
coroutine on the server's event loop, parked on an ``asyncio.Event``
(``toto.core.live``) — it asks nothing while held, and it has closed its
database connection (``_let_go``). Django gives every request under ASGI a
thread of its own for its synchronous parts (the middleware, the look); that
thread sleeps for the length of the hold. It is not one of a fixed pool, so
held requests never keep other requests waiting. ``wait.MAX_WAITS`` caps
them per account.

**Only where the server can hold.** Under WSGI a held request would be a
whole worker, so there the door never holds: it answers at once and tells
the page when to ask again (``retry``).
"""

from __future__ import annotations

import asyncio

from asgiref.sync import sync_to_async
from django.http import HttpResponseNotAllowed, JsonResponse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from toto.core import live

from . import services, wait


def _json(data, status=200):
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "no-store"
    return response


def _refusal(request, *, reads: bool = False):
    """What stops a request before its door: one from another site (a write,
    or with ``reads`` any request), or nobody signed in. ``None`` to
    proceed."""
    from toto.api.fetch_metadata import cross_site_refusal

    refused = cross_site_refusal(request, reads=reads)
    if refused is not None:
        refused["Cache-Control"] = "no-store"
        return refused
    if not getattr(request.user, "is_authenticated", False):
        return _json({"error": _("Not authenticated.")}, status=401)
    return None


@require_GET
def api_list(request):
    """The latest notifications and the unread count."""
    refused = _refusal(request)
    if refused is not None:
        return refused
    rows, unread = services.listing(request.user)
    return _json({"unread": unread, "items": [services.payload(row) for row in rows]})


@require_POST
def api_read(request):
    """Mark one of the caller's notifications read (``id`` in the body)."""
    refused = _refusal(request)
    if refused is not None:
        return refused
    raw = (request.POST.get("id") or "").strip()
    if not (raw.isascii() and raw.isdigit()) or len(raw) > 18:
        return _json({"error": _("Not found.")}, status=404)
    if not services.mark_read(request.user, int(raw)):
        return _json({"error": _("Not found.")}, status=404)
    return _json({"ok": True, "unread": services.unread_count(request.user)})


@require_POST
def api_read_all(request):
    """Mark every one of the caller's notifications read."""
    refused = _refusal(request)
    if refused is not None:
        return refused
    services.mark_all_read(request.user)
    return _json({"ok": True, "unread": 0})


# ---------------------------------------------------------------------------
# The long poll
# ---------------------------------------------------------------------------

def _let_go() -> None:
    """A held request keeps no database connection: this thread's are closed
    (the next query opens one again). Never inside a transaction — the
    tests' — where closing would lose it."""
    from django.db import connections

    for connection in connections.all(initialized_only=True):
        if not connection.in_atomic_block:
            connection.close()


def _enter(request, waiter):
    """The request's synchronous half, on its own thread: who asks, about
    what, and where they stand. ``(refusal, None)`` or ``(None, (user,
    folder ids, cursor, look, answer or None))``."""
    refused = _refusal(request, reads=True)
    if refused is not None:
        return refused, None
    user = request.user
    ids = wait.folder_ids(request.GET.get("folders"))
    cursor = wait.read_cursor(user, request.GET.get("cursor"))
    folders = wait.watched(user, ids)
    # Listen first, look second: nothing said in between is missed.
    waiter.listen(wait.keys_for(user, folders))
    found = wait.look(user, folders)
    data = wait.answer(user, cursor, found)
    if data is None:
        _let_go()
    return None, (user, ids, cursor, found, data)


def _look_again(user, ids, cursor, *, anyway=False):
    """After a wake, or when the hold ends: the access check and the look
    once more, and what there is to say."""
    found = wait.look(user, wait.watched(user, ids))
    data = wait.answer(user, cursor, found, anyway=anyway)
    _let_go()
    return found, data


async def api_wait(request):
    """The long poll. See the module docstring."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    on_thread = sync_to_async(thread_sensitive=True)
    waiter = live.Waiter(asyncio.get_running_loop())
    try:
        refused, asked = await on_thread(_enter)(request, waiter)
        if refused is not None:
            return refused
        user, ids, cursor, found, data = asked
        if data is not None:
            return _json(data)
        # An ASGI request has a scope; under WSGI holding would hold a worker.
        seconds = wait.hold_seconds() if hasattr(request, "scope") else 0.0
        if seconds <= 0:
            return _json(wait.sent_home(user, found))
        slot = wait.claim(user.pk, waiter)
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + seconds
            while True:
                woken = await live.wait(waiter, deadline - loop.time(), seen=found.seen)
                if slot.bumped:
                    return _json(wait.sent_home(user, found))
                waiter.reset()
                found, data = await on_thread(_look_again)(user, ids, cursor, anyway=not woken)
                if data is not None:
                    return _json(data)
        finally:
            wait.release(user.pk, slot)
    finally:
        waiter.close()
