import base64
import jwt
import uuid
from django.test import TestCase, Client
from federal.models import Federation, FederatedIdentity, Challenge, FederalAuthGateway, RefreshToken
from gervazy.models import RSAKeyPair, SecretKey
from federal.guard import FederalGuard
from oya.models import Platform


class AuthFlowTests(TestCase):
    def setUp(self):
        platform = Platform.objects.create(
            site_name="TestSite",
            publication_year=2025,
            active=True,
        )
        self.federation = Federation.objects.create(
            name="TestFed",
            slug="testfed",
            platform=platform
        )
        self.secret = SecretKey.objects.create(size=64, key="supersecret", passphrase="admin")
        self.gateway = FederalAuthGateway.objects.create(federation=self.federation, secret=self.secret)

        # Create identity with dummy RSA keypair
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

        self.guard = FederalGuard(self.gateway)
        self.client = Client()

    def test_initiate_login_creates_challenge(self):
        challenge = self.guard.initiate_login(self.identity)
        self.assertIsInstance(challenge, Challenge)
        self.assertEqual(challenge.identity, self.identity)
        self.assertFalse(challenge.verified)

    def test_verify_login_fails_without_signature(self):
        challenge = self.guard.initiate_login(self.identity)
        # invalid signature
        result = self.guard.verify_login(self.identity, base64.b64encode(b"bad").decode())
        self.assertFalse(result)

    def test_issue_access_and_refresh_tokens(self):
        access = self.guard.issue_access_token(self.identity)
        refresh = self.guard.issue_refresh_token(self.identity)
        self.assertTrue(isinstance(access, str))
        self.assertTrue(isinstance(refresh, str))

        # decode access token
        payload = jwt.decode(access, self.secret.key, algorithms=["HS256"])
        self.assertEqual(payload["sub"], str(self.identity.id))
        self.assertEqual(payload["scope"], "access")

        # refresh token persisted
        rt = RefreshToken.objects.get(token=refresh)
        self.assertEqual(rt.identity, self.identity)
        self.assertFalse(rt.revoked)

    def test_refresh_access_token_success(self):
        refresh = self.guard.issue_refresh_token(self.identity)
        new_access = self.guard.refresh_access_token(refresh)
        self.assertTrue(isinstance(new_access, str))
        payload = jwt.decode(new_access, self.secret.key, algorithms=["HS256"])
        self.assertEqual(payload["scope"], "access")

    def test_refresh_access_token_fails_with_inactive_gateway(self):
        refresh = self.guard.issue_refresh_token(self.identity)
        self.gateway.active = False
        self.gateway.save()

        guard = FederalGuard(self.gateway)
        new_access = guard.refresh_access_token(refresh)
        self.assertIsNone(new_access)
