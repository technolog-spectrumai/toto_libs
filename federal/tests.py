from django.test import TestCase, Client
from unittest.mock import MagicMock
from datetime import datetime, timedelta
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from django.urls import reverse

from federal.token import TokenService, Payload, Token, sign_token
from federal.models import (
    Federation,
    AuthRSAKeyPair,
    LocalIdentityProvider,
    FederatedIdentity
)


class TokenServiceTests(TestCase):
    def setUp(self):
        # Mock federation
        self.federation = MagicMock()
        self.federation.url = "https://local-idp.example.com"

        # Generate real RSA key
        private_key_obj = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = private_key_obj.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )

        self.rsa_key = MagicMock()
        self.rsa_key.get_private_key.return_value = pem.decode()
        self.rsa_key.key_id = "mock-key-id"
        self.rsa_key.to_jwk.return_value = {
            "kty": "RSA",
            "kid": "mock-key-id",
            "alg": "RS256",
            "use": "sig",
            "n": "mock-n",
            "e": "mock-e"
        }

        self.provider = MagicMock()
        self.provider.issuer_url = self.federation.url
        self.provider.audience = "my-app"
        self.provider.token_lifetime = 600
        self.provider.rsa_key = self.rsa_key
        self.provider.__class__ = LocalIdentityProvider

        self.identity = MagicMock()
        self.identity.subject = "user-123"
        self.identity.federation = self.federation
        self.identity.email = "user@example.com"
        self.identity.name = "Test User"
        self.identity.last_seen = datetime.utcnow()

    def test_payload_to_dict(self):
        now = datetime.utcnow()
        payload = Payload(
            subject="user-1",
            issuer="https://issuer",
            audience="aud",
            issued_at=now,
            expires_at=now + timedelta(seconds=300)
        )
        data = payload.to_dict()
        self.assertEqual(data["sub"], "user-1")
        self.assertIsInstance(data["iat"], int)
        self.assertIsInstance(data["exp"], int)

    def test_payload_expiration(self):
        now = datetime.utcnow()
        expired_payload = Payload(
            subject="expired-user",
            issuer="https://issuer",
            audience="aud",
            issued_at=now - timedelta(seconds=600),
            expires_at=now - timedelta(seconds=1)
        )
        self.assertTrue(expired_payload.is_expired())

    def test_token_inherits_payload(self):
        now = datetime.utcnow()
        token = Token(
            value="jwt-token",
            subject="user-abc",
            issuer="https://issuer",
            audience="aud",
            issued_at=now,
            expires_at=now + timedelta(seconds=300)
        )
        self.assertEqual(token.subject, "user-abc")
        self.assertFalse(token.is_expired())

    def test_token_serialization(self):
        now = datetime.utcnow()
        token = Token(
            value="jwt-token",
            subject="user-xyz",
            issuer="https://issuer",
            audience="aud",
            issued_at=now,
            expires_at=now + timedelta(seconds=300)
        )
        serialized = token.serialize()
        self.assertEqual(serialized["token"], "jwt-token")
        self.assertEqual(serialized["sub"], "user-xyz")
        self.assertIn("iat", serialized)
        self.assertIn("exp", serialized)

    def test_sign_token(self):
        now = datetime.utcnow()
        payload = Payload(
            subject="user-abc",
            issuer="https://issuer",
            audience="aud",
            issued_at=now,
            expires_at=now + timedelta(seconds=300)
        )
        token = sign_token(payload, self.rsa_key.get_private_key(), self.rsa_key.key_id)
        self.assertIsInstance(token, Token)
        self.assertEqual(token.subject, "user-abc")
        self.assertIsNotNone(token.value)

    def test_issue_token(self):
        service = TokenService(self.provider)
        token = service.issue_token(self.identity)
        self.assertIsInstance(token, Token)
        self.assertEqual(token.subject, "user-123")
        self.assertFalse(token.is_expired())

    def test_fetch_jwks_local(self):
        service = TokenService(self.provider)
        keys = service.fetch_jwks()
        self.assertIsInstance(keys, list)
        self.assertEqual(keys[0]["kid"], "mock-key-id")


class TokenViewTests(TestCase):
    def setUp(self):
        self.client = Client()

        self.federation = Federation.objects.create(
            name="Test Federation",
            url="https://local-idp.example.com",
            jwks_url="https://local-idp.example.com/.well-known/jwks.json",
            active=True
        )

        self.rsa_key = AuthRSAKeyPair.generate(
            key_id="test-key-id",
            issuer=self.federation.url
        )
        self.rsa_key.save()

        self.provider = LocalIdentityProvider.objects.create(
            name="Test Provider",
            issuer_url=self.federation.url,
            audience="my-app",
            rsa_key=self.rsa_key,
            federation=self.federation,
            contact_email="admin@example.com",
            token_lifetime=600,
            active=True
        )

        self.identity = FederatedIdentity.objects.create(
            subject="user-123",
            email="user@example.com",
            name="Test User",
            federation=self.federation
        )

    def test_issue_token_view(self):
        url = reverse("federal:issue-token")
        response = self.client.post(
            url,
            data={
                "provider_id": self.provider.id,
                "federated_id": self.identity.id
            },
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("token", response.json())

    def test_verify_token_view(self):
        service = TokenService(self.provider)
        token = service.issue_token(self.identity)

        url = reverse("federal:verify-token")
        response = self.client.post(
            url,
            data={
                "provider_id": self.provider.id,
                "token": token.value
            },
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["active"])
        self.assertEqual(response.json()["claims"]["sub"], self.identity.subject)
