"""Framework-level API base views and the data-mesh access gate.

These were historically defined in the chat app's ``api_views``, but they are
foundational: ``CorsApiView`` / ``MeshGatedApiView`` are subclassed by the
always-on apps (vault, events, locations, socialhub) and must not drag
in the chat app's ``channels`` dependency. They live here in the always-on
``toto.api`` app so the basic (no-studio) tier can import them.

Import them from here. The backwards-compatibility re-export that used to live in
the chat app was removed when its auth/identity views moved into ``toto.api``.
"""
from urllib.parse import urlparse

from django.http import HttpResponse, JsonResponse
from django.views import View

from .tokens import DOOR_API, user_for_session_key

CORS_ALLOW_HEADERS = "Content-Type, X-Requested-With, Authorization"
CORS_ALLOW_METHODS = "GET, POST, OPTIONS"


def _is_allowed_origin(origin: str) -> bool:
    """Allow localhost / 127.0.0.1 (any port) and Tauri scheme origins."""
    if not origin:
        return False
    try:
        parsed = urlparse(origin)
        hostname = parsed.hostname or ""
        return hostname in ("localhost", "127.0.0.1") or parsed.scheme == "tauri"
    except Exception:
        return False


def _cors(request, response):
    origin = request.META.get("HTTP_ORIGIN", "")
    if _is_allowed_origin(origin):
        response["Access-Control-Allow-Origin"] = origin
        response["Access-Control-Allow-Credentials"] = "true"
    response["Access-Control-Allow-Headers"] = CORS_ALLOW_HEADERS
    response["Access-Control-Allow-Methods"] = CORS_ALLOW_METHODS
    return response


def _try_bearer_auth(request):
    """Authenticate request via Authorization: Bearer <session_key> header.

    The key is checked as a cookie would be — the backend's ``get_user`` and
    the session hash — in ``tokens.user_for_session_key`` (2026-09-30). A key
    it refuses leaves the request exactly as anonymous as no header at all.
    """
    if request.user.is_authenticated:
        return
    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth_header.startswith("Bearer "):
        return
    session_key = auth_header[7:].strip()
    if not session_key:
        return
    user = user_for_session_key(session_key, door=DOOR_API, request=request)
    if user is None:
        return
    request.user = user
    # Marked, so a cross-site guard can tell a token from a cookie: a
    # Bearer header is never sent by a browser on its own.
    request._toto_bearer_auth = True


def bearer_key(request) -> str:
    """The Bearer key this request was signed in by, or ``""`` (2026-09-30).

    Only when the header is what signed it in: a request a cookie signed in
    ignores the header, and so does this.
    """
    if not getattr(request, "_toto_bearer_auth", False):
        return ""
    return request.META.get("HTTP_AUTHORIZATION", "")[7:].strip()


def bearer_auth(request) -> bool:
    """Resolve a Bearer token on ``request``; is it signed in afterwards?

    The public name of the door above, for a host whose own gate runs before
    the view does (2026-09-30): zenobia's login gate answers in
    ``process_view``, ahead of ``CorsApiView.dispatch``, so it has to ask
    here or the desktop's token never reaches a JSON door.
    """
    _try_bearer_auth(request)
    return bool(getattr(request.user, "is_authenticated", False))


class CorsApiView(View):
    """Base view: handles CORS preflight, injects CORS headers, and accepts Bearer auth."""

    def dispatch(self, request, *args, **kwargs):
        if request.method == "OPTIONS":
            return _cors(request, HttpResponse())
        _try_bearer_auth(request)
        response = super().dispatch(request, *args, **kwargs)
        return _cors(request, response)


# ── Decentralized data mesh: server access-gate ────────────────────────────────
#
# A single Django group ("data_mesh") decides who may read the gated data *from the
# server*. Members get it; everyone else is denied and must pull it peer-to-peer from
# someone who has it (the Enigma "Sync" feature) — building a decentralized mesh where a
# few server-privileged seed users fan the data out to everyone else.

DATA_MESH_GROUP = "data_mesh"


def ensure_data_mesh_group():
    """The `data_mesh` group, created if missing (idempotent).

    api's migration 0002_data_mesh_group seeds it on every fresh database;
    init_data calls this too, so the seed does not live only in a migration.
    """
    from django.contrib.auth.models import Group

    return Group.objects.get_or_create(name=DATA_MESH_GROUP)[0]

# The domains behind the gate (informational; all share the one group).
MESH_DOMAINS = ["missions", "tasks", "locations", "events", "people"]


def in_data_mesh(user) -> bool:
    """True if `user` may read gated data directly from the server."""
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and user.groups.filter(name=DATA_MESH_GROUP).exists()
    )


class MeshGatedApiView(CorsApiView):
    """A CorsApiView whose **reads (GET)** are gated by `data_mesh` membership. Non-members
    get 403 `{"gated": true}` so the client keeps its local (possibly peer-pulled) copy and
    prompts a peer pull. Writes (POST/PATCH/DELETE) are unaffected."""

    def dispatch(self, request, *args, **kwargs):
        if request.method == "OPTIONS":
            return _cors(request, HttpResponse())
        _try_bearer_auth(request)
        if request.method == "GET" and not in_data_mesh(getattr(request, "user", None)):
            if not request.user or not request.user.is_authenticated:
                return _cors(request, JsonResponse({"error": "Not authenticated."}, status=401))
            return _cors(request, JsonResponse({
                "error": "server access gated",
                "gated": True,
                "detail": "This data is part of the mesh — pull it from a peer who has it.",
            }, status=403))
        return super().dispatch(request, *args, **kwargs)


def render_access_denied(request, status=403):
    """Nice HTML access-denied page for browser (template) access to gated data."""
    from django.shortcuts import render
    return render(request, "api/access_denied.html", {"group": DATA_MESH_GROUP}, status=status)


def mesh_required(view_func):
    """Decorator for HTML (template) views: members see the page; others get the nice
    access-denied template instead."""
    from functools import wraps

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not in_data_mesh(getattr(request, "user", None)):
            return render_access_denied(request)
        return view_func(request, *args, **kwargs)

    return _wrapped
