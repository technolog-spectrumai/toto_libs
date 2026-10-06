"""Geography's doors: JSON in, JSON out, POST only.

Every door wants a signed-in member's session cookie and Django's CSRF
token, answers a refusal as JSON (400, 402, 403, 404, 405, 409, 429, 503)
with a sentence under ``error``, and sends ``Cache-Control: no-store``.

Each view carries ``geography_door``, the mark of who it lets in:
``"signed-in"`` (any member, for their own request or their own point) or
``"moderator"`` (a community's head or an administrator). A walk over this
URLconf fails on a route without one (stage 64's access test).

The pages that draw the map are the profile and the community page
(``plugins``); this module serves no HTML.
"""

from __future__ import annotations

import json
from functools import wraps

from django.http import JsonResponse
from django.utils.translation import gettext as _

from . import access, places, routing, saves
from .charging import Refusal

MAX_BODY = 256 * 1024


def _answer(payload, status=200, retry_after=None):
    response = JsonResponse(payload, status=status)
    response["Cache-Control"] = "no-store"
    if retry_after:
        response["Retry-After"] = str(retry_after)
    return response


def _refuse(message, status, retry_after=None):
    return _answer({"error": str(message)}, status, retry_after)


def door(mark):
    """A JSON door: POST, a member, an object for a body; refusals as JSON."""

    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method != "POST":
                response = _refuse(_("This address takes POST only."), 405)
                response["Allow"] = "POST"
                return response
            if not request.user.is_authenticated:
                return _refuse(_("Sign in to use the map."), 403)
            if len(request.body) > MAX_BODY:
                return _refuse(_("That request is too large."), 400)
            try:
                data = json.loads(request.body.decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError, RecursionError):
                # RecursionError: a body of nothing but open brackets, nested
                # past the parser's depth. It is no ValueError.
                data = None
            if not isinstance(data, dict):
                return _refuse(_("Send a JSON object."), 400)
            try:
                return _answer(view(request, data, *args, **kwargs))
            except places.REFUSALS as exc:
                return _refuse(exc, getattr(exc, "status_code", 400),
                               getattr(exc, "retry_after", None))

        wrapped.geography_door = mark
        return wrapped

    return decorate


def _person(request):
    from toto.socialhub.permissions import current_person

    person = current_person(request)
    if person is None:
        raise Refusal(_("You have no profile yet."), 404)
    return person


def _community(request, slug):
    """The community, for whoever may set its headquarters: 404 for a slug
    that names none, 403 for everybody but its head and an administrator."""
    from toto.socialhub.models import Community

    community = Community.objects.filter(slug=slug).first()
    if community is None:
        raise Refusal(_("Community not found."), 404)
    if not access.may_set_headquarters(request.user, community):
        raise Refusal(_("Only the community's head sets its headquarters and zone."), 403)
    return community


@door("signed-in")
def search(request, data):
    results, charged = places.search(request.user, data.get("q"), data.get("op"))
    return {"results": results, "charged": charged}


@door("signed-in")
def route(request, data):
    return routing.route(request.user, data.get("from"), data.get("to"),
                         data.get("mode"), data.get("op"))


@door("signed-in")
def my_address(request, data):
    state, charged = saves.save_person_point(
        request.user, _person(request), lat=data.get("lat"), lng=data.get("lng"),
        name=data.get("name"), note=data.get("note"), op=data.get("op"))
    return {"address": state, "charged": charged}


@door("signed-in")
def my_address_clear(request, data):
    return {"removed": saves.clear_person_point(request.user, _person(request))}


@door("moderator")
def headquarters(request, data, slug):
    community = _community(request, slug)
    state, charged = saves.save_headquarters(
        request.user, community, lat=data.get("lat"), lng=data.get("lng"),
        name=data.get("name"), note=data.get("note"), op=data.get("op"))
    return {"headquarters": state, "charged": charged}


@door("moderator")
def headquarters_clear(request, data, slug):
    return {"removed": saves.clear_headquarters(request.user, _community(request, slug))}


@door("moderator")
def zone(request, data, slug):
    community = _community(request, slug)
    state, charged = saves.save_zone(
        request.user, community, name=data.get("name"),
        description=data.get("description"), outline=data.get("outline"),
        op=data.get("op"))
    return {"zone": state, "charged": charged}


@door("moderator")
def zone_clear(request, data, slug):
    return {"removed": saves.clear_zone(request.user, _community(request, slug))}
