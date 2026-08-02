"""Machine endpoints for the peer platform. HTTP Basic over TLS, csrf-exempt —
the standing convention for server-to-server surfaces. Auth resolves the ONE
active LedgerPeer; a cheap SHA-256 fingerprint rejects junk before the
constant-time secret compare (the unauthenticated-endpoint CPU lesson)."""

from __future__ import annotations

import base64
import binascii
import json

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import LedgerPeer
from .services import handshake as handshake_service


def _resolve_peer(request) -> LedgerPeer | None:
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if not header.startswith("Basic "):
        return None
    try:
        raw = base64.b64decode(header[6:]).decode()
        client_id, _, secret = raw.partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return None
    if not client_id or not secret:
        return None
    peer = LedgerPeer.objects.filter(client_id=client_id).first()
    if peer is None or peer.status == LedgerPeer.STATUS_SUSPENDED:
        # Suspended peers still fail auth-shaped: no oracle for row existence.
        return None
    if not peer.check_secret(secret):
        return None
    return peer


def peer_auth(view):
    """Decorator: resolve + require the authenticated peer."""

    def wrapper(request, *args, **kwargs):
        peer = _resolve_peer(request)
        if peer is None:
            response = HttpResponse(status=401)
            response["WWW-Authenticate"] = 'Basic realm="clearing"'
            return response
        return view(request, peer, *args, **kwargs)

    return wrapper


@csrf_exempt
@require_POST
@peer_auth
def handshake(request, peer):
    try:
        envelope = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "invalid json"}, status=400)
    result = handshake_service.apply_hello(peer, envelope)
    if result["status"] in ("bad-signature",):
        return JsonResponse(result, status=403)
    # Reply with our own hello so one round trip pins both directions.
    peer.refresh_from_db()
    result["hello"] = handshake_service.build_hello(peer)
    return JsonResponse(result)
