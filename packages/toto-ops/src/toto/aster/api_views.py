"""Aster signalling HTTP API.

All views subclass ``toto.api.cors.CorsApiView`` so they share the same
``Authorization: Bearer <session_key>`` auth + Tauri/localhost CORS handling the
rest of the edge API uses, and are ``csrf_exempt`` (the enigma client talks over
Tor with a bearer token, not a cookie/CSRF pair).

Endpoints (all require an authenticated user):
  POST /aster/device          {node_id, label?}        register/refresh my device
  POST /aster/addr            {node_id, relay_url}      publish my device's relay URL
  GET  /aster/addr/<node_id>                            resolve a fresh relay URL
  GET  /aster/resolve?user=<username>                   list a user's device node_ids
"""
import json

from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from toto.api.cors import CorsApiView

from .models import AsterAddress, AsterDevice


def _require_auth(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return JsonResponse({"error": "Not authenticated."}, status=401)
    return None


def _json_body(request):
    try:
        return json.loads(request.body or b"{}"), None
    except (ValueError, TypeError):
        return None, JsonResponse({"error": "Invalid JSON."}, status=400)


@method_decorator(csrf_exempt, name="dispatch")
class DeviceView(CorsApiView):
    """POST /aster/device — register (or refresh) this user's device NodeId."""

    def post(self, request):
        if (err := _require_auth(request)):
            return err
        data, err = _json_body(request)
        if err:
            return err
        node_id = (data.get("node_id") or "").strip()
        if not node_id:
            return JsonResponse({"error": "node_id required."}, status=400)

        existing = AsterDevice.objects.filter(node_id=node_id).first()
        if existing and existing.user_id != request.user.id:
            # A NodeId is an unforgeable pubkey; reject cross-user re-registration.
            return JsonResponse({"error": "node_id registered to another user."}, status=409)

        device, _ = AsterDevice.objects.update_or_create(
            node_id=node_id,
            defaults={
                "user": request.user,
                "kind": (data.get("kind") or AsterDevice.KIND_DEFAULT)[:32],
                "label": (data.get("label") or "")[:120],
            },
        )
        return JsonResponse({"ok": True, "node_id": device.node_id, "kind": device.kind})


@method_decorator(csrf_exempt, name="dispatch")
class AddrView(CorsApiView):
    """POST /aster/addr — publish my device's relay URL.
    GET /aster/addr/<node_id> — resolve a peer's fresh relay URL."""

    def post(self, request, node_id=None):
        if (err := _require_auth(request)):
            return err
        data, err = _json_body(request)
        if err:
            return err
        nid = (node_id or data.get("node_id") or "").strip()
        relay_url = (data.get("relay_url") or "").strip()
        if not nid or not relay_url:
            return JsonResponse({"error": "node_id and relay_url required."}, status=400)

        device = AsterDevice.objects.filter(node_id=nid, user=request.user).first()
        if not device:
            return JsonResponse(
                {"error": "Unknown device — register it via /aster/device first."},
                status=403,
            )
        AsterAddress.objects.update_or_create(device=device, defaults={"relay_url": relay_url})
        device.save(update_fields=["last_seen"])  # auto_now bump → presence
        return JsonResponse({"ok": True})

    def get(self, request, node_id=None):
        if (err := _require_auth(request)):
            return err
        nid = (node_id or "").strip()
        if not nid:
            return JsonResponse({"error": "node_id required."}, status=400)
        addr = (
            AsterAddress.objects.select_related("device")
            .filter(device__node_id=nid)
            .first()
        )
        if not addr or not addr.is_fresh():
            return JsonResponse({"error": "No fresh address."}, status=404)
        return JsonResponse({"node_id": nid, "relay_url": addr.relay_url})


@method_decorator(csrf_exempt, name="dispatch")
class ResolveView(CorsApiView):
    """GET /aster/resolve?user=<username>[&kind=vox|gossip] — a user's device NodeIds.

    Optional ``kind`` narrows to one iroh service (e.g. the user's Vox endpoint),
    so a caller can pick the right NodeId to connect to.
    """

    def get(self, request):
        if (err := _require_auth(request)):
            return err
        username = (request.GET.get("user") or "").strip()
        if not username:
            return JsonResponse({"error": "user required."}, status=400)
        User = get_user_model()
        user = User.objects.filter(username=username).first()
        if not user:
            return JsonResponse({"node_ids": []})
        qs = AsterDevice.objects.filter(user=user)
        kind = (request.GET.get("kind") or "").strip()
        if kind:
            qs = qs.filter(kind=kind)
        node_ids = list(qs.values_list("node_id", flat=True))
        return JsonResponse({"node_ids": node_ids})
