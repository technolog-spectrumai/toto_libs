"""Per-transport reachability gate for faros.

nginx stamps every proxied request with ``X-Faros-Transport: onion|clearnet`` (see
deploy.py — the onion has its own internal listener, so the header is unspoofable: a
clearnet client can only reach the clearnet listener). This middleware turns the
clearnet switch off live by returning 404 to clearnet-origin requests when
``NomadSettings.clearnet_enabled`` is False (and, defensively, the onion side too —
though disabling the onion really unpublishes it).

Requests without the header (local dev, the in-container healthcheck hitting
web:8000 directly) are always allowed.
"""
from django.http import HttpResponseNotFound

from .models import NomadSettings

TRANSPORT_HEADER = "HTTP_X_FAROS_TRANSPORT"


class NomadReachabilityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        transport = request.META.get(TRANSPORT_HEADER)
        if transport in ("onion", "clearnet"):
            settings = NomadSettings.load()  # cached
            enabled = settings.onion_enabled if transport == "onion" else settings.clearnet_enabled
            if not enabled:
                return HttpResponseNotFound()
        return self.get_response(request)
