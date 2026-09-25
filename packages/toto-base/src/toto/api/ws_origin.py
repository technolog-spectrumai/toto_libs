"""Which origins may open a WebSocket to this host.

Without a check, a page on any origin can open ``ws/forum/…`` with the
member's cookies and read or post as them (zenobia/todo.md, item 1). Channels'
own `AllowedHostsOriginValidator` closes that, but it also refuses a request
with NO Origin header — which is every non-browser client, including the
shipped desktop app that authenticates with ``?token=``. Browsers always send
Origin on a WebSocket handshake, so allowing its absence re-opens nothing.

Allowed: no Origin; an origin whose host is in ALLOWED_HOSTS; the ``tauri``
scheme the desktop app uses (the same rule as `cors._is_allowed_origin`).
"""

from __future__ import annotations

from urllib.parse import urlparse

from channels.security.websocket import OriginValidator


class TotoOriginValidator(OriginValidator):
    def __init__(self, application):
        super().__init__(application, allowed_origins=[])

    def valid_origin(self, parsed_origin):
        if parsed_origin is None:
            return True
        if parsed_origin.scheme == "tauri":
            return True
        from django.conf import settings
        from django.http.request import is_same_domain

        host = parsed_origin.hostname or ""
        allowed = list(getattr(settings, "ALLOWED_HOSTS", []) or [])
        if settings.DEBUG and not allowed:
            allowed = [".localhost", "127.0.0.1", "[::1]"]
        return any(pattern == "*" or is_same_domain(host, pattern) for pattern in allowed)


def allowed_origins_validator(application):
    return TotoOriginValidator(application)


def origin_of(headers) -> object:
    """The parsed Origin header of an ASGI scope's headers, or None."""
    for name, value in headers or []:
        if name == b"origin":
            try:
                return urlparse(value.decode("latin1"))
            except UnicodeDecodeError:
                return None
    return None
