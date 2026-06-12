from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import redirect
from django.views import View

from . import service


class _SuperuserView(View):
    """Base: POST-only, superuser-only, redirect back to the referring page."""

    def dispatch(self, request, *args, **kwargs):
        if not (request.user.is_authenticated and request.user.is_superuser):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def _back(self, request):
        return redirect(request.META.get("HTTP_REFERER") or "sso:my_profile")


class ConnectQrView(_SuperuserView):
    """Serve the server-rendered PNG QR of the current connect (.onion) URL.

    Superuser-only — the QR encodes the hidden .onion address, so it must not be
    fetchable by anonymous visitors (e.g. over clearnet). Rendered server-side so
    it works in Tor Browser, which blocks the JS that drew it client-side.
    """

    def get(self, request):
        png = service.ensure_connect_qr()
        if png is None:
            raise Http404("No onion published.")
        return FileResponse(open(png, "rb"), content_type="image/png")


class MigrateOnionView(_SuperuserView):
    """Mint a fresh onion and retire the current one."""

    def post(self, request):
        try:
            new_onion = service.migrate_onion(triggered_by=request.user)
            messages.success(request, f"Onion migrated. New address: {new_onion}.onion")
        except Exception as exc:  # noqa: BLE001 — surface any control-port failure to the admin
            messages.error(request, f"Onion migration failed: {exc}")
        return self._back(request)


class SetReachabilityView(_SuperuserView):
    """Toggle a transport on/off: POST transport=onion|clearnet, enabled=1|0."""

    def post(self, request):
        transport = request.POST.get("transport")
        enabled = request.POST.get("enabled") in ("1", "true", "on")
        try:
            if transport == service.ONION:
                service.set_onion_enabled(enabled, by=request.user)
            elif transport == service.CLEARNET:
                service.set_clearnet_enabled(enabled, by=request.user)
            else:
                raise ValueError(f"Unknown transport: {transport!r}")
            messages.success(
                request,
                f"{transport.capitalize()} reachability {'enabled' if enabled else 'disabled'}.",
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        except Exception as exc:  # noqa: BLE001 — control-port failure when toggling the onion
            messages.error(request, f"Could not change {transport} reachability: {exc}")
        return self._back(request)
