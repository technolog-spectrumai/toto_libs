"""Which origins may open a WebSocket to this host.

Without a check, a page on any origin can open ``ws/forum/…`` with the
member's cookies and read or post as them (zenobia/todo.md, item 1). Channels'
own `AllowedHostsOriginValidator` closes that, but it also refuses a request
with NO Origin header — which is every non-browser client, including the
shipped desktop app that authenticates with ``?token=``. Browsers always send
Origin on a WebSocket handshake, so allowing its absence re-opens nothing.

Allowed: no Origin; an origin whose host is in ALLOWED_HOSTS; the ``tauri``
scheme the desktop app uses (the same rule as `cors._is_allowed_origin`).

**A socket that only a cookie signs in** takes the stricter
``same_origin_validator`` (2026-10-04, the live socket, ``ws/live/``). The rule
above goes by host name alone, so on a machine that runs the local stack a
page served from another port of ``localhost`` passes it — and a browser sends
that page's handshake the member's cookie, because a port does not make
another site. The stricter rule is the one Django's CSRF check applies to a
cookie write: the Origin must be this very origin (scheme, host and port, as
the request names itself) or one of ``CSRF_TRUSTED_ORIGINS``, and it must be
THERE — a browser always sends it on a handshake, and no other client holds
the cookie.
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


# ---------------------------------------------------------------------------
# The cookie-only socket's rule
# ---------------------------------------------------------------------------

def _header(scope, name: bytes) -> str:
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            try:
                return value.decode("latin1")
            except UnicodeDecodeError:
                return ""
    return ""


def _is_secure(scope) -> bool:
    """Whether the browser reached this handshake over TLS — the scope's own
    scheme, or the proxy's header the host trusts (``SECURE_PROXY_SSL_HEADER``),
    as ``HttpRequest.is_secure`` decides it."""
    from django.conf import settings

    header = getattr(settings, "SECURE_PROXY_SSL_HEADER", None)
    if header:
        name, secure_value = header
        wire = name[5:] if name.startswith("HTTP_") else name
        value = _header(scope, wire.lower().replace("_", "-").encode("latin1"))
        if value:
            return value.split(",")[0].strip() == secure_value
    return scope.get("scheme") in ("wss", "https")


def is_same_origin(scope) -> bool:
    """Does this handshake's Origin name this platform's own origin?

    Django's CSRF rule for an Origin header (``CsrfViewMiddleware.
    _origin_verified``), on an ASGI scope: exactly ``<scheme>://<Host>`` as the
    request names itself, or an entry of ``CSRF_TRUSTED_ORIGINS`` (exact, or
    its ``https://*.example.org`` form). No Origin is a refusal.
    """
    from urllib.parse import urlsplit

    from django.conf import settings
    from django.http.request import is_same_domain, split_domain_port, validate_host

    origin = _header(scope, b"origin").strip()
    if not origin or origin.lower() == "null":
        return False
    host = _header(scope, b"host").strip()
    allowed_hosts = list(getattr(settings, "ALLOWED_HOSTS", []) or [])
    if settings.DEBUG and not allowed_hosts:
        allowed_hosts = [".localhost", "127.0.0.1", "[::1]"]
    # A Host this platform does not answer to names no origin of its own
    # (HttpRequest.get_host raises DisallowedHost there).
    name = split_domain_port(host)[0] if host else ""
    if name and validate_host(name, allowed_hosts):
        good = f"{'https' if _is_secure(scope) else 'http'}://{host}"
        if origin.lower() == good.lower():
            return True
    trusted = list(getattr(settings, "CSRF_TRUSTED_ORIGINS", []) or [])
    if origin in trusted:
        return True
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    for entry in trusted:
        if "*" not in entry:
            continue
        try:
            pattern = urlsplit(entry)
        except ValueError:
            continue
        if pattern.scheme == parsed.scheme and is_same_domain(
                parsed.netloc, pattern.netloc.lstrip("*")):
            return True
    return False


class SameOriginValidator:
    """ASGI middleware: a WebSocket handshake whose Origin is not this
    platform's own is refused before anything else runs (HTTP 403)."""

    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "websocket":
            raise ValueError("SameOriginValidator guards WebSocket connections only")
        if is_same_origin(scope):
            return await self.application(scope, receive, send)
        from channels.security.websocket import WebsocketDenier

        return await WebsocketDenier()(scope, receive, send)


def same_origin_validator(application):
    return SameOriginValidator(application)
