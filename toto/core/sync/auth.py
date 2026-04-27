from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from toto.core.models import Platform


class PlatformSyncAuthentication(BaseAuthentication):
    """
    Authenticates sync requests using Platform.api_secret.
    """

    class SyncUser:
        """Minimal user-like object for DRF permission checks."""
        @property
        def is_authenticated(self):
            return True

    def authenticate(self, request):
        auth = request.headers.get("Authorization")

        if not auth or not auth.startswith("Token "):
            raise AuthenticationFailed("Missing Authorization: Token <key>")

        token = auth.split(" ", 1)[1].strip()

        passphrase = getattr(settings, "PLATFORM_PASSPHRASE", None)
        if not passphrase:
            raise AuthenticationFailed("Server misconfigured: PLATFORM_PASSPHRASE missing")

        # Check all active platforms
        for platform in Platform.objects.filter(active=True):
            try:
                decrypted = platform.api_secret.get_key(passphrase)
            except Exception:
                continue

            if decrypted == token:
                # Return a minimal authenticated user + platform as auth
                return (self.SyncUser(), platform)

        raise AuthenticationFailed("Invalid API token")
