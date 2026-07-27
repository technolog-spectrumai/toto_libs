from django.conf import settings

from .last_visited import record_and_get_back


def last_visited(request):
    if not request.user.is_authenticated:
        return {}
    back_url, back_name = record_and_get_back(request.user.pk, request.path)
    return {"last_visited_url": back_url, "last_visited_name": back_name}


def build_flags(request):
    """Expose build-tier flags so templates can gate optional UI (e.g. the
    'Ask Steven' Knowledge-Graph tab only when the sabbia backend is built)."""
    return {
        "BUILD_SABBIA": bool(getattr(settings, "BUILD_SABBIA", False)),
        "BUILD_CONNECTORS": bool(getattr(settings, "BUILD_CONNECTORS", False)),
        "GRAFANA_ENABLED": bool(getattr(settings, "GRAFANA_ENABLED", False)),
        "GITEA_ENABLED": bool(getattr(settings, "GITEA_ENABLED", False)),
    }
