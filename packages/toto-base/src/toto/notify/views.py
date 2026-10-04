"""The bell's three doors (2026-10-04): the list, mark one read, mark all read.

Each acts on ``request.user`` and on nobody else — no door takes an account's
id, and a notification that is not the caller's answers 404, the same as one
that does not exist. They are the session's doors: a signed-in cookie, the
CSRF token on the two writes (they are NOT ``csrf_exempt``), and the
Fetch-Metadata guard in front of both writes (``toto.api.fetch_metadata``),
so a write a browser sent from another site is refused before anything is
read. JSON in and out, never cached.

The list is drawn here, at reading time: in the reader's language and time
zone, and less what they may no longer be told (``services.listing``).
"""

from __future__ import annotations

from django.http import JsonResponse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from . import services


def _json(data, status=200):
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "no-store"
    return response


def _refusal(request):
    """What stops a request before its door: a write from another site, or
    nobody signed in. ``None`` to proceed."""
    from toto.api.fetch_metadata import cross_site_refusal

    refused = cross_site_refusal(request)
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
