"""The WebSocket's token door.

A socket that the session cookie left anonymous is signed in by a token: the
session key ``/api/login/`` hands the desktop client, checked like a cookie
through ``toto.api.tokens`` (2026-09-30). A refused token leaves the socket
anonymous, exactly as no token would.

The token is presented one of two ways:

- **In the subprotocol header** (2026-10-01), the way to use: the client
  offers two subprotocols, ``toto.bearer`` and then the key —
  ``new WebSocket(url, ["toto.bearer", key])`` in a browser,
  ``Sec-WebSocket-Protocol: toto.bearer, <key>`` from any other client. The
  server answers ``toto.bearer``, never the key: a browser that offered
  subprotocols closes a socket whose answer names none of them, and the key
  belongs in no response. The consumer behind sees the offer without the key.
- **As ``?token=<key>`` in the URL**, today's desktop clients. It works as
  before, but a URL is what logs keep, so nginx's access log keeps no query
  string since the same day (deploy.py) and neither do uvicorn's own lines
  (``server_logs``). It goes once the desktop client has moved to the header.

When both are present, the header's key is the one checked.
"""

from urllib.parse import parse_qs

from channels.db import database_sync_to_async

from . import server_logs

#: The subprotocol a client offers in front of its key, and the one the server
#: answers with. The key itself is never answered.
BEARER_SUBPROTOCOL = "toto.bearer"

_PROTOCOL_HEADER = b"sec-websocket-protocol"


@database_sync_to_async
def _user_from_token(token):
    """Resolve a session-key token to a User, or AnonymousUser.

    Through the same check as the JSON doors' Bearer header
    (``toto.api.tokens``, 2026-09-30): an inactive account and a session
    signed before a password change are refused, and a refused token is as
    anonymous as none.
    """
    from django.contrib.auth.models import AnonymousUser

    from .tokens import DOOR_WEBSOCKET, user_for_session_key

    return user_for_session_key(token, door=DOOR_WEBSOCKET) or AnonymousUser()


def bearer_offer(subprotocols):
    """``(key, offer)``: the key offered after ``toto.bearer``, and the offer
    without it.

    The key is ``None`` when the client did not offer ``toto.bearer`` or named
    nothing after it; the offer is then returned as it came.
    """
    offer = [p for p in subprotocols or () if isinstance(p, str) and p]
    if BEARER_SUBPROTOCOL not in offer:
        return None, offer
    at = offer.index(BEARER_SUBPROTOCOL) + 1
    if at >= len(offer):
        return None, offer
    return offer[at], offer[:at] + offer[at + 1:]


def _without_key(scope, offer):
    """The consumer's scope: the offer, and its header, without the key."""
    headers = [(name, value) for name, value in scope.get("headers", [])
               if name.lower() != _PROTOCOL_HEADER]
    if offer:
        headers.append((_PROTOCOL_HEADER, ", ".join(offer).encode("latin1")))
    return dict(scope, subprotocols=offer, headers=headers)


def _answering_bearer(send, key):
    """``send``, with the socket's accept answering ``toto.bearer``.

    Every consumer in the library accepts without naming a subprotocol, which
    a browser that offered some takes for a failed handshake. One that names
    its own (from the offer) keeps it, unless it names the key.
    """
    async def send_answering_bearer(message):
        if message.get("type") == "websocket.accept":
            chosen = message.get("subprotocol")
            if not chosen or chosen == key:
                message = dict(message, subprotocol=BEARER_SUBPROTOCOL)
        return await send(message)

    return send_answering_bearer


class TokenAuthMiddleware:
    """
    WebSocket middleware: if session-cookie auth left the user anonymous,
    sign the socket in by the key offered after the ``toto.bearer``
    subprotocol, or else by ``?token=<session_key>`` in the query string.
    """

    def __init__(self, inner):
        self.inner = inner
        # uvicorn names every socket's path in its log WITH the query string,
        # ?token= included; its lines keep the path alone (2026-10-01). And
        # its access line names every request's path, a vault peer's grant
        # and token with it: those are cut as [token] (37c.24).
        server_logs.keep_no_query_string()
        server_logs.keep_no_path_secrets()

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "websocket":
            key, offer = bearer_offer(scope.get("subprotocols"))
            if BEARER_SUBPROTOCOL in offer:
                send = _answering_bearer(send, key)
            scope = _without_key(scope, offer) if key else dict(scope)
            if key is None:
                qs = scope.get("query_string", b"").decode()
                key = parse_qs(qs).get("token", [None])[0]
            user = scope.get("user")
            if key and not getattr(user, "is_authenticated", False):
                scope["user"] = await _user_from_token(key)
        return await self.inner(scope, receive, send)
