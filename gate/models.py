import datetime
from oya.models import Platform
from gervazy.models import RSAKeyPair, SecretKey
import uuid
from django.db import models
from django.utils import timezone
from federal.models import FederatedIdentity, Federation
from django.contrib.auth.models import User


class Challenge(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )
    identity = models.ForeignKey(
        FederatedIdentity,
        on_delete=models.CASCADE,
        related_name="challenges"
    )
    nonce = models.CharField(max_length=255, unique=True)
    issued_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    verified = models.BooleanField(default=False)

    def __str__(self):
        return f"Challenge for {self.identity.id} issued at {self.issued_at}"

    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def verify(self, signature: bytes) -> bool:
        """
        Verify the challenge by checking the signature against the nonce
        using the identity's RSA public key.
        """
        if self.is_expired():
            return False

        rsa_pair = getattr(self.identity, "rsa_keypair", None)
        if not rsa_pair:
            return False

        # Delegate verification to RSAKeyPair.verify
        valid = rsa_pair.verify(self.nonce.encode(), signature)
        if valid:
            self.verified = True
            self.save(update_fields=["verified"])
        return valid

    def save(self, *args, **kwargs):
        if not self.nonce:
            self.nonce = uuid.uuid4().hex
        if not self.expires_at:
            self.expires_at = timezone.now() + datetime.timedelta(seconds=300)
        super().save(*args, **kwargs)


class RefreshToken(models.Model):
    """
    Persisted refresh tokens for session management.
    """
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="refresh_tokens",
        help_text="User this refresh token belongs to"
    )
    token = models.CharField(
        max_length=512,
        unique=True,
        help_text="The actual refresh token string (JWT or opaque)"
    )
    issued_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked = models.BooleanField(default=False)

    def __str__(self):
        return f"RefreshToken for {self.user} (revoked={self.revoked})"

    def is_expired(self) -> bool:
        """
        Check if the token has expired.
        """
        return timezone.now() >= self.expires_at

    def revoke(self):
        """
        Mark this token as revoked.
        """
        self.revoked = True
        self.save(update_fields=["revoked"])

