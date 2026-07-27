from urllib.parse import parse_qs

from channels.db import database_sync_to_async


@database_sync_to_async
def _user_from_token(token):
    """Resolve a session-key Bearer token to a User, or AnonymousUser."""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore

    try:
        store = SessionStore(session_key=token)
        user_id = store.get("_auth_user_id")
        if not user_id:
            return AnonymousUser()
        User = get_user_model()
        return User.objects.get(pk=user_id)
    except Exception:
        return AnonymousUser()


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
