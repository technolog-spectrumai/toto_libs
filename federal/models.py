from django.db import models
from django.contrib.auth.models import User
import jwt
from gervazy.models import RSAKeyPair


class Federation(models.Model):
    name = models.CharField(max_length=100, unique=True)
    url = models.URLField(unique=True, help_text="Federation's issuer URL")
    jwks_url = models.URLField()
    description = models.TextField(blank=True)
    logo = models.ImageField(
        upload_to='federation_logos/',
        null=True,
        blank=True,
        help_text="Optional logo for this federation"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


# 🔐 RSA Key Pair Management
class AuthRSAKeyPair(RSAKeyPair):
    active = models.BooleanField(default=True)

    def to_jwk(self):
        pub_key = self.get_public_key()
        numbers = pub_key.public_numbers()
        return {
            "kty": "RSA",
            "kid": self.key_id,
            "use": "sig",
            "alg": "RS256",
            "n": jwt.utils.base64url_encode(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, 'big')).decode(),
            "e": jwt.utils.base64url_encode(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, 'big')).decode()
        }


class FederatedIdentity(models.Model):
    subject = models.CharField(max_length=100)
    email = models.EmailField(null=True, blank=True)
    name = models.CharField(max_length=100, null=True, blank=True)
    last_seen = models.DateTimeField(auto_now=True)
    federation = models.ForeignKey(
        Federation,
        on_delete=models.PROTECT,
        related_name='federated_identities',
        help_text="Federation this identity belongs to"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["subject", "federation"], name="unique_subject_federation")
        ]

    @property
    def issuer(self):
        return self.federation.url

    def __str__(self):
        return f"{self.name or self.subject} from {self.issuer}"


class BaseIdentityProvider(models.Model):
    name = models.CharField(max_length=100)
    issuer_url = models.URLField(unique=True)
    audience = models.CharField(max_length=100)

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.name} ({self.issuer_url})"


# 🏠 Local Identity Provider (your own)
class LocalIdentityProvider(BaseIdentityProvider):
    rsa_key = models.ForeignKey(AuthRSAKeyPair, on_delete=models.PROTECT)
    contact_email = models.EmailField()
    metadata_url = models.URLField(null=True, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    token_lifetime = models.PositiveIntegerField(default=300, help_text="Token lifetime in seconds")
    federation = models.ForeignKey(
        Federation,
        on_delete=models.PROTECT,
        related_name='local_identity_providers',
        help_text="Federation this provider belongs to"
    )

    def get_jwk_set(self):
        return {
            "keys": [self.rsa_key.to_jwk()]
        }

    @property
    def issuer(self):
        return self.federation.url


class ExternalIdentityProvider(BaseIdentityProvider):
    trusted = models.BooleanField(default=True)
    last_verified = models.DateTimeField(null=True, blank=True)
    federation = models.ForeignKey(
        Federation,
        on_delete=models.PROTECT,
        related_name='external_identity_providers',
        help_text="Federation this provider belongs to"
    )

    @property
    def jwks_url(self):
        return self.federation.jwks_url

    def fetch_jwk_set(self):
        import requests
        response = requests.get(self.jwks_url)
        response.raise_for_status()
        return response.json()


# Link Between Local and Federated Identities
class UserFederationLink(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='federated_links')
    federated_user = models.ForeignKey(FederatedIdentity, on_delete=models.CASCADE, related_name='local_links')
    linked_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=True)

    class Meta:
        unique_together = ('user', 'federated_user')

    def __str__(self):
        return f"{self.user.username} ↔ {self.federated_user.subject} @ {self.federated_user.issuer}"
