from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


FALLBACK_TRANSPORTS = {
    "default": "noosphere.transports.RequestsTransport",
}


def get_transport_map():
    """
    Return deployment-defined transport map.

    Choices are intentionally sourced from settings.NOOSPHERE_TRANSPORTS.
    If the setting is missing, only "default" is available.
    """
    transports = getattr(settings, "NOOSPHERE_TRANSPORTS", None)

    if not transports:
        return FALLBACK_TRANSPORTS

    return dict(transports)


def get_transport_choices():
    return [
        (key, key)
        for key in sorted(get_transport_map().keys())
    ]


def get_transport_class(backend_key):
    transports = get_transport_map()
    dotted_path = transports.get(backend_key)

    if not dotted_path:
        allowed = ", ".join(sorted(transports.keys()))
        raise ImproperlyConfigured(
            f"No Noosphere transport configured for backend key '{backend_key}'. "
            f"Allowed values: {allowed}"
        )

    return import_string(dotted_path)


def get_transport_for_rule(rule):
    backend_key = rule.remote_platform.get_backend_key_for_direction(rule.direction)
    transport_class = get_transport_class(backend_key)

    return transport_class(
        remote_platform=rule.remote_platform,
        rule=rule,
    )
