from django.urls import reverse
from oya.models import Platform
from gervazy.models import RSAKeyPair, SecretKey
import uuid
import datetime
from django.db import models
from django.utils import timezone


class Federation(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(
        max_length=100,
        unique=True,
        help_text="Unique slug identifier for this federation"
    )
    description = models.TextField(blank=True)
    logo = models.ImageField(
        upload_to='federation_logos/',
        null=True,
        blank=True,
        help_text="Optional logo for this federation"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=True)
    platform = models.OneToOneField(
        Platform,
        on_delete=models.CASCADE,
        related_name="federation",
        help_text="Platform associated with this federation"
    )

    def __str__(self):
        return self.name

    @property
    def url(self):
        return reverse("federal:federation_detail_json", kwargs={"slug": self.slug})


class FederatedIdentity(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )
    name = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    federation = models.ForeignKey(
        "Federation",
        on_delete=models.PROTECT,
        related_name="federated_identities",
        help_text="Federation this identity belongs to"
    )
    rsa_keypair = models.OneToOneField(   # 🔐 link to RSAKeyPair
        RSAKeyPair,
        on_delete=models.CASCADE,
        related_name="identity",
        null=True,
        blank=True,
        help_text="RSA keypair associated with this identity"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["id", "federation"],
                name="unique_id_federation"
            )
        ]

    @property
    def issuer(self):
        return self.federation.url

    def __str__(self):
        return f"{self.name or self.id} from {self.issuer}"


class Challenge(models.Model):
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
        return f"Challenge for {self.identity.did} issued at {self.issued_at}"

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


class FederalAuthGateway(models.Model):
    """
    Minimal gateway: links a Federation to its signing SecretKey.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    federation = models.OneToOneField(
        "Federation",
        on_delete=models.CASCADE,
        related_name="auth_gateway",
        help_text="Federation this gateway belongs to"
    )
    secret = models.OneToOneField(
        SecretKey,
        on_delete=models.CASCADE,
        related_name="gateway",
        help_text="SecretKey used for signing tokens"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"AuthGateway for {self.federation.name}"

    @property
    def key_material(self) -> str:
        """
        Return the actual secret key material for signing.
        """
        return self.secret.key

