from django.db import models
from django.contrib.auth.models import User
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from datetime import datetime, timedelta
import jwt
import requests


# 🔐 RSA Key Management
class RSAKey(models.Model):
    key_id = models.CharField(max_length=100, unique=True)
    public_key_pem = models.TextField()
    private_key_pem = models.TextField()
    issuer = models.URLField(default='https://your-project.example.com')
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"RSAKey {self.key_id}"

    def get_private_key(self):
        return serialization.load_pem_private_key(
            self.private_key_pem.encode(),
            password=None,
            backend=default_backend()
        )

    def get_public_key(self):
        return serialization.load_pem_public_key(
            self.public_key_pem.encode(),
            backend=default_backend()
        )

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


# 🌐 Trusted Identity Issuers
class TrustedIssuer(models.Model):
    name = models.CharField(max_length=100)
    issuer_url = models.URLField(unique=True)
    jwks_url = models.URLField()
    audience = models.CharField(max_length=100)
    trusted = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    def fetch_jwks(self):
        try:
            response = requests.get(self.jwks_url)
            return response.json().get('keys', [])
        except Exception:
            return []


# 👤 Federated Identity
class FederatedIdentity(models.Model):
    subject = models.CharField(max_length=100, unique=True)
    issuer = models.URLField()
    email = models.EmailField(null=True, blank=True)
    name = models.CharField(max_length=100, null=True, blank=True)
    last_seen = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name or self.subject} from {self.issuer}"


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


# 🪪 Base Token Model
class BaseToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    token = models.TextField()
    issued_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    audience = models.CharField(max_length=100)
    issuer = models.URLField()

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.__class__.__name__} for {self.user.username}"

    def is_valid(self):
        try:
            decoded = jwt.decode(self.token, options={"verify_signature": False})
            return datetime.utcnow() < datetime.utcfromtimestamp(decoded['exp'])
        except Exception:
            return False

    def sign_token(self, rsa_key: RSAKey, payload: dict, algorithm='RS256', expires_in=300):
        payload.update({
            'exp': datetime.utcnow() + timedelta(seconds=expires_in),
            'iat': datetime.utcnow(),
            'iss': rsa_key.issuer,
            'aud': self.audience,
            'sub': str(self.user.id)
        })
        token = jwt.encode(payload, rsa_key.get_private_key(), algorithm=algorithm, headers={'kid': rsa_key.key_id})
        self.token = token
        self.expires_at = datetime.utcfromtimestamp(payload['exp'])
        return token


# 🪪 Identity Token
class IdentityToken(BaseToken):
    class Meta:
        verbose_name = "ID Token"
        verbose_name_plural = "ID Tokens"


# 🔐 Access Token
class AccessToken(BaseToken):
    class Meta:
        verbose_name = "Access Token"
        verbose_name_plural = "Access Tokens"
