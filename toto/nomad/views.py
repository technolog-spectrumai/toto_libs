from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.views import View

from . import service


class MigrateOnionView(View):
    """POST-only, superuser-only: mint a fresh onion and retire the current one."""

    def dispatch(self, request, *args, **kwargs):
        if not (request.user.is_authenticated and request.user.is_superuser):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def post(self, request):
        try:
            new_onion = service.migrate_onion(triggered_by=request.user)
            messages.success(request, f"Onion migrated. New address: {new_onion}.onion")
        except Exception as exc:  # noqa: BLE001 — surface any control-port failure to the admin
            messages.error(request, f"Onion migration failed: {exc}")
        return redirect(request.META.get("HTTP_REFERER") or "sso:my_profile")
