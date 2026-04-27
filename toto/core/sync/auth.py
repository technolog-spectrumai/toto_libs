from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from toto.core.models import Platform


class PlatformSyncAuthentication(BaseAuthentication):
    """
    Authenticates sync requests using Platform.api_secret.
    """

    def authenticate(self, request):
        auth = request.headers.get("Authorization")

        if not auth or not auth.startswith("Token "):
            raise AuthenticationFailed("Missing Authorization: Token <key>")

        token = auth.split(" ", 1)[1].strip()

        passphrase = getattr(settings, "PLATFORM_PASSPHRASE", None)
        if not passphrase:
            raise AuthenticationFailed("Server misconfigured: PLATFORM_PASSPHRASE missing")

        for platform in Platform.objects.filter(active=True):
            try:
                decrypted = platform.api_secret.get_key(passphrase)
            except Exception:
                continue

            if decrypted == token:
                if not platform.api_owner:
                    raise AuthenticationFailed("Platform has no API owner assigned")

                # DRF expects (user, auth)
                return (platform.api_owner, platform)

        raise AuthenticationFailed("Invalid API token")
