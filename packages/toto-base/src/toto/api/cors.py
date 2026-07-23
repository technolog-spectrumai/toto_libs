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

from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.http import HttpResponse, JsonResponse
from django.views import View

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
    """Authenticate request via Authorization: Bearer <session_key> header."""
    if request.user.is_authenticated:
        return
    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth_header.startswith("Bearer "):
        return
    session_key = auth_header[7:].strip()
    if not session_key:
        return
    try:
        store = SessionStore(session_key=session_key)
        user_id = store.get("_auth_user_id")
        if not user_id:
            return
        User = get_user_model()
        request.user = User.objects.get(pk=user_id)
    except Exception:
        pass


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
