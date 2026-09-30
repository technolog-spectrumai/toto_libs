from urllib.parse import parse_qs

from channels.db import database_sync_to_async


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


class TokenAuthMiddleware:
    """
    WebSocket middleware: if session-cookie auth left the user anonymous,
    fall back to ?token=<session_key> in the query string.
    """

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "websocket":
            user = scope.get("user")
            if not getattr(user, "is_authenticated", False):
                qs = scope.get("query_string", b"").decode()
                token = parse_qs(qs).get("token", [None])[0]
                if token:
                    scope["user"] = await _user_from_token(token)
        return await self.inner(scope, receive, send)
