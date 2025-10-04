from django.test import TestCase
from unittest.mock import MagicMock
from datetime import datetime, timedelta
from federal.token import TokenService, Payload, Token, sign_token
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from federal.models import LocalIdentityProvider


class TokenServiceTests(TestCase):
    def setUp(self):
        # Generate a real RSA key for signing
        private_key_obj = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048
        )
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
        self.provider.issuer_url = "https://local-idp.example.com"
        self.provider.audience = "my-app"
        self.provider.token_lifetime = 600
        self.provider.rsa_key = self.rsa_key
        self.provider.__class__ = LocalIdentityProvider

        self.identity = MagicMock()
        self.identity.subject = "user-123"
        self.identity.issuer = self.provider.issuer_url
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
