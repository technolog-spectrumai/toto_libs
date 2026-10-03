from django.conf import settings

from .last_visited import record_and_get_back


def last_visited(request):
    """The "back to …" link: the app section a member came from.

    A request may carry no ``user`` (2026-10-03). Django draws its error pages
    with the request, so every context processor runs — and the 400 page for
    an unknown host (``DisallowedHost``, raised by CommonMiddleware) is drawn
    before AuthenticationMiddleware has run. Reading ``request.user`` raised
    there, and every such request answered 500. Django's own ``auth``
    processor reads it the same way.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    back_url, back_name = record_and_get_back(user.pk, request.path)
    return {"last_visited_url": back_url, "last_visited_name": back_name}


def build_flags(request):
    """Expose build-tier flags so templates can gate optional UI (e.g. the
    'Ask Steven' Knowledge-Graph tab only when the sabbia backend is built)."""
    return {
        "BUILD_SABBIA": bool(getattr(settings, "BUILD_SABBIA", False)),
        "BUILD_CONNECTORS": bool(getattr(settings, "BUILD_CONNECTORS", False)),
        "BUILD_WEASYPRINT": bool(getattr(settings, "BUILD_WEASYPRINT", False)),
        "GRAFANA_ENABLED": bool(getattr(settings, "GRAFANA_ENABLED", False)),
        "GITEA_ENABLED": bool(getattr(settings, "GITEA_ENABLED", False)),
    }
