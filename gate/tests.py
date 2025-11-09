import jwt
import datetime
from django.test import TestCase
from django.utils import timezone
from django.contrib.auth.models import User
from oya.models import Platform
from gervazy.models import RSAKeyPair, SecretKey
from federal.models import Federation, FederatedIdentity
from gate.models import Challenge, RefreshToken


class AuthFlowTests(TestCase):
    def setUp(self):
        # SecretKey + Platform
        self.secret = SecretKey.objects.create(
            size=64,
            key="supersecret",
            passphrase="admin"
        )
        self.platform = Platform.objects.create(
            site_name="TestSite",
            publication_year=2025,
            active=True,
            secret=self.secret
        )

        # Federation + Identity
        self.federation = Federation.objects.create(
            name="TestFed",
            slug="testfed",
            platform=self.platform
        )
        self.rsa = RSAKeyPair.generate(
            key_id="test-key",
            issuer=self.federation.url
        )
        self.rsa.save()
        self.identity = FederatedIdentity.objects.create(
            federation=self.federation,
            rsa_keypair=self.rsa,
            name="Alice"
        )

        # User for refresh tokens
        self.user = User.objects.create_user(username="alice", password="password")

    def test_challenge_creation_and_expiry(self):
        challenge = Challenge.objects.create(identity=self.identity)
        self.assertIsNotNone(challenge.nonce)
        self.assertFalse(challenge.verified)
        self.assertFalse(challenge.is_expired())

        # Force expiry
        challenge.expires_at = timezone.now() - datetime.timedelta(seconds=1)
        challenge.save()
        self.assertTrue(challenge.is_expired())

    def test_challenge_verification_fails_without_rsa(self):
        challenge = Challenge.objects.create(identity=self.identity)
        # Temporarily remove rsa_keypair
        self.identity.rsa_keypair = None
        self.identity.save()
        result = challenge.verify(b"fake_signature")
        self.assertFalse(result)

    def test_refresh_token_lifecycle(self):
        rt = RefreshToken.objects.create(
            user=self.user,
            token="dummy-refresh",
            expires_at=timezone.now() + datetime.timedelta(seconds=60)
        )
        self.assertFalse(rt.revoked)
        self.assertFalse(rt.is_expired())

        # Expire it
        rt.expires_at = timezone.now() - datetime.timedelta(seconds=1)
        rt.save()
        self.assertTrue(rt.is_expired())

        # Revoke it
        rt.revoke()
        self.assertTrue(rt.revoked)

    def test_jwt_signed_with_platform_secret(self):
        payload = {
            "sub": str(self.identity.id),
            "scope": "access",
            "iat": int(timezone.now().timestamp())
        }
        token = jwt.encode(payload, self.platform.secret.key, algorithm="HS256")
        decoded = jwt.decode(token, self.platform.secret.key, algorithms=["HS256"])
        self.assertEqual(decoded["sub"], str(self.identity.id))
        self.assertEqual(decoded["scope"], "access")
