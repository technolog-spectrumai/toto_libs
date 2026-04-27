import base64
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from toto.core.models import Platform
from django.utils import timezone
from datetime import timedelta


class PlatformSyncAuthentication(BaseAuthentication):
    """
    Authenticates incoming sync requests using the platform's IN keypair.
    """

    def authenticate(self, request):
        signature_header = request.headers.get("Authorization")
        platform_id = request.headers.get("X-Platform-ID")
        timestamp = request.headers.get("X-Timestamp")

        if not signature_header or not signature_header.startswith("Signature "):
            raise AuthenticationFailed("Missing Authorization: Signature <sig>")

        if not platform_id:
            raise AuthenticationFailed("Missing X-Platform-ID")

        if not timestamp:
            raise AuthenticationFailed("Missing X-Timestamp")

        # Prevent replay attacks
        ts = timezone.datetime.fromisoformat(timestamp)
        if timezone.now() - ts > timedelta(minutes=5):
            raise AuthenticationFailed("Timestamp too old")

        signature = base64.b64decode(signature_header.split(" ", 1)[1])

        # Identify platform by IN keypair
        try:
            platform = Platform.objects.get(
                api_keypair_in__key_id=platform_id,
                active=True
            )
        except Platform.DoesNotExist:
            raise AuthenticationFailed("Unknown platform")

        public_key = platform.api_keypair_in.get_public_key()
        data = timestamp.encode()

        try:
            public_key.verify(
                signature,
                data,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH
                ),
                hashes.SHA256()
            )
        except Exception:
            raise AuthenticationFailed("Invalid RSA signature")

        if not platform.api_owner:
            raise AuthenticationFailed("Platform has no API owner assigned")

        return (platform.api_owner, platform)

