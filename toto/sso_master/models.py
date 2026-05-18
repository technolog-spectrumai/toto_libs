import secrets
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone

User = get_user_model()


class SSOClient(models.Model):
    """
    A server/application that is allowed to use this Django project as SSO.

    This is the OpenID Connect relying party / client registration record.
    """

    CONFIDENTIAL = "confidential"
    PUBLIC = "public"

    CLIENT_TYPES = [
        (CONFIDENTIAL, "Confidential"),
        (PUBLIC, "Public"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    client_id = models.CharField(max_length=128, unique=True)
    client_secret_hash = models.CharField(max_length=255, blank=True)

    name = models.CharField(max_length=255)
    client_type = models.CharField(max_length=20, choices=CLIENT_TYPES, default=CONFIDENTIAL)

    redirect_uris = models.TextField(help_text="One redirect URI per line. Must match exactly.")
    allowed_scopes = models.CharField(max_length=255, default="openid email profile")

    active = models.BooleanField(default=True)
    trusted = models.BooleanField(
        default=False,
        help_text="If true, skip the consent page. Use only for first-party/internal apps.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.client_id})"

    def set_client_secret(self, raw_secret=None):
        """
        Store a hashed client secret and return the raw value once.
        Do not store raw client secrets.
        """
        raw_secret = raw_secret or secrets.token_urlsafe(48)
        self.client_secret_hash = make_password(raw_secret)
        return raw_secret

    def verify_client_secret(self, raw_secret):
        if self.client_type == self.PUBLIC:
            return True
        if not raw_secret:
            return False
        return check_password(raw_secret, self.client_secret_hash)

    def redirect_uri_list(self):
        return [uri.strip() for uri in self.redirect_uris.splitlines() if uri.strip()]

    def is_redirect_uri_allowed(self, redirect_uri):
        return redirect_uri in self.redirect_uri_list()

    def scope_list(self):
        return [scope.strip() for scope in self.allowed_scopes.split() if scope.strip()]


class SSOSubject(models.Model):
    """
    Stable public subject identifier for a local user.

    Do not expose User.pk as OIDC `sub`.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="sso_subject")
    subject = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user}: {self.subject}"


class SSOAuthorizationCode(models.Model):
    """
    Short-lived one-time code issued by /sso/authorize/ and consumed by /sso/token/.
    """

    code = models.CharField(max_length=128, unique=True)
    client = models.ForeignKey(SSOClient, on_delete=models.CASCADE, related_name="authorization_codes")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sso_authorization_codes")

    redirect_uri = models.URLField(max_length=1000)
    scope = models.CharField(max_length=255)
    nonce = models.CharField(max_length=255, blank=True, null=True)
    state = models.CharField(max_length=255, blank=True, null=True)

    code_challenge = models.CharField(max_length=255, blank=True, null=True)
    code_challenge_method = models.CharField(max_length=20, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = secrets.token_urlsafe(48)
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(minutes=5)
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def is_used(self):
        return self.used_at is not None

    def mark_used(self):
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])

    def __str__(self):
        return f"Code for {self.user} / {self.client}"


class SSOSigningKey(models.Model):
    """
    RSA signing key pair for OIDC ID tokens.

    The public key is stored in plaintext for fast JWKS responses.
    The private key is stored encrypted in Gervazy (EncryptedPrivateKey).
    Decryption requires the SSO_VAULT_PASSWORD setting.
    """

    key_id = models.CharField(max_length=100, unique=True)
    algorithm = models.CharField(max_length=16, default="RS256")
    public_key_pem = models.TextField()
    encrypted_key = models.OneToOneField(
        "gervazy.EncryptedPrivateKey",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sso_signing_key",
        help_text="Gervazy EncryptedPrivateKey holding the RSA private key.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.key_id} ({'active' if self.is_active else 'inactive'})"


class SSOAccessToken(models.Model):
    """
    Opaque bearer token used by /sso/userinfo/.
    """

    token = models.CharField(max_length=255, unique=True)
    client = models.ForeignKey(SSOClient, on_delete=models.CASCADE, related_name="access_tokens")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sso_access_tokens")
    scope = models.CharField(max_length=255)

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(48)
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(hours=1)
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def is_revoked(self):
        return self.revoked_at is not None

    def revoke(self):
        self.revoked_at = timezone.now()
        self.save(update_fields=["revoked_at"])

    def __str__(self):
        return f"Access token for {self.user} / {self.client}"


class SSORelyingParty(SSOClient):
    """
    First-class relying-party name for the OIDC client registration table.

    This proxy avoids duplicating client credentials while exposing the domain
    concept used by OIDC relying-party onboarding and provisioning.
    """

    class Meta:
        proxy = True
        ordering = ["name"]
        verbose_name = "SSO relying party"
        verbose_name_plural = "SSO relying parties"
