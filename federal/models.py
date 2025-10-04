from django.db import models
from django.contrib.auth.models import User
import jwt
from gervazy.models import RsaKeyPair


# 🔐 RSA Key Pair Management
class AuthRSAKeyPair(RsaKeyPair):
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

# 👤 Federated Identity
class FederatedIdentity(models.Model):
    subject = models.CharField(max_length=100)
    issuer = models.URLField()
    email = models.EmailField(null=True, blank=True)
    name = models.CharField(max_length=100, null=True, blank=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('subject', 'issuer')

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


class IdentityProvider(models.Model):
    name = models.CharField(max_length=100)
    issuer_url = models.URLField(unique=True)
    audience = models.CharField(max_length=100)
    trusted = models.BooleanField(default=True)
    rsa_key = models.ForeignKey(AuthRSAKeyPair, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return self.name


# 🏠 Local Identity Provider (your own)
class LocalIdentityProvider(BaseIdentityProvider):
    rsa_key = models.ForeignKey(AuthRSAKeyPair, on_delete=models.PROTECT)
    contact_email = models.EmailField()
    metadata_url = models.URLField(null=True, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    token_lifetime = models.PositiveIntegerField(default=300, help_text="Token lifetime in seconds")

    def get_jwk_set(self):
        return {
            "keys": [self.rsa_key.to_jwk()]
        }


class ExternalIdentityProvider(BaseIdentityProvider):
    jwks_url = models.URLField()
    trusted = models.BooleanField(default=True)
    last_verified = models.DateTimeField(null=True, blank=True)

    def fetch_jwk_set(self):
        import requests
        response = requests.get(self.jwks_url)
        response.raise_for_status()
        return response.json()


# 🔗 Link Between Local and Federated Identities
class UserFederationLink(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='federated_links')
    federated_user = models.ForeignKey(FederatedIdentity, on_delete=models.CASCADE, related_name='local_links')
    linked_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=True)

    class Meta:
        unique_together = ('user', 'federated_user')

    def __str__(self):
        return f"{self.user.username} ↔ {self.federated_user.subject} @ {self.federated_user.issuer}"
