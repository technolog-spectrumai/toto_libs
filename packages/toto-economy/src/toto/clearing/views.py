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

from .models import ClearingOutbox, LedgerPeer
from .services import bridge as bridge_service
from .services import handshake as handshake_service
from .services import transfers as transfers_service


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


@csrf_exempt
@require_POST
@peer_auth
def inbox(request, peer):
    """Accept one signed message and apply it exactly once.

    The response carries any envelope WE produced in reaction (a fulfill
    receipt, a reject) as ``reply``, so the common case settles in one round
    trip; the same envelope also sits in our outbox, so a lost response is
    redelivered rather than lost.
    """
    try:
        envelope = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "invalid json"}, status=400)

    before = ClearingOutbox.objects.filter(peer=peer).count()
    try:
        result = bridge_service.receive(peer, envelope, transfers_service.dispatch)
    except bridge_service.InboxRefusal as refusal:
        status = 409 if refusal.status == "idempotency-conflict" else 400
        if refusal.status == "bad-signature":
            status = 403
        body = {"status": refusal.status, "reason": refusal.reason}
        body.update(_reply_envelope(peer, before))
        return JsonResponse(body, status=status)

    body = dict(result)
    body.update(_reply_envelope(peer, before))
    return JsonResponse(body)


def _reply_envelope(peer, before_count: int) -> dict:
    """The newest envelope this exchange produced, if any."""
    if ClearingOutbox.objects.filter(peer=peer).count() <= before_count:
        return {}
    row = ClearingOutbox.objects.filter(peer=peer).order_by("-seq").first()
    if row is None:
        return {}
    # It has been handed over in this response; mark it acked so the sweeper
    # does not send it a second time (the receiver dedupes anyway).
    ClearingOutbox.objects.filter(pk=row.pk, state=ClearingOutbox.QUEUED).update(
        state=ClearingOutbox.ACKED)
    return {"reply": row.envelope()}
