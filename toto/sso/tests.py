"""
Unit and integration tests for the SSO app and the Gervazy crypto layer.

Run with:
    python manage.py test toto.sso --settings=portal.settings -v 2
"""
import base64
import os
import time

import jwt
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.gervazy.crypto import (
    GervazyCryptoSession,
    aes_gcm_decrypt,
    aes_gcm_encrypt,
)
from toto.gervazy.models import (
    EncryptedSecret,
    UserStrongbox,
    VaultMasterKey,
    WrappedDataKey,
)
from toto.sso.models import SSOAuthorizationCode, SSOClient, SSORelyingParty, SSOSigningKey, SSOSubject
from toto.sso.provisioning import RelyingPartyProvisioningError, create_relying_party
from toto.sso.services import (
    build_id_token,
    get_issuer,
    get_jwks,
    get_user_claims,
    verify_pkce,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VAULT_PASSWORD = "test-sso-vault-password-1234"


def _make_rsa_pair(key_size=2048):
    """Generate an RSA key pair; returns (private_pem_str, public_pem_str)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def _setup_sso_signing_key(admin_user, private_pem, public_pem, vault_password=_VAULT_PASSWORD):
    """
    Create a gervazy strongbox + encrypted private key + SSOSigningKey.
    Returns (signing_key, session, wrapped_key).
    """
    session, wrapped_key = GervazyCryptoSession.initialize_strongbox(
        admin_user, "sso-system-strongbox", vault_password
    )
    epk = session.encrypt_private_key(
        wrapped_key,
        private_pem,
        key_id="test-sso-key-2026",
        key_type="RSA-2048",
        public_key_pem=public_pem,
        aad=b"sso-signing-key:test-sso-key-2026",
    )
    signing_key = SSOSigningKey.objects.create(
        key_id="test-sso-key-2026",
        public_key_pem=public_pem,
        encrypted_key=epk,
        is_active=True,
    )
    return signing_key, session, wrapped_key


# ---------------------------------------------------------------------------
# 1. AES-GCM primitive tests
# ---------------------------------------------------------------------------

class TestAesGcmPrimitives(TestCase):

    def test_encrypt_decrypt_roundtrip(self):
        key = os.urandom(32)
        plaintext = b"hello gervazy"
        ciphertext, nonce = aes_gcm_encrypt(key, plaintext)
        self.assertEqual(aes_gcm_decrypt(key, ciphertext, nonce), plaintext)

    def test_roundtrip_with_aad(self):
        key = os.urandom(32)
        plaintext = b"authenticated message"
        aad = b"context:user-42"
        ciphertext, nonce = aes_gcm_encrypt(key, plaintext, aad)
        self.assertEqual(aes_gcm_decrypt(key, ciphertext, nonce, aad), plaintext)

    def test_wrong_aad_rejected(self):
        key = os.urandom(32)
        ciphertext, nonce = aes_gcm_encrypt(key, b"data", b"real-aad")
        with self.assertRaises(InvalidTag):
            aes_gcm_decrypt(key, ciphertext, nonce, b"wrong-aad")

    def test_wrong_key_rejected(self):
        key = os.urandom(32)
        ciphertext, nonce = aes_gcm_encrypt(key, b"data")
        with self.assertRaises(InvalidTag):
            aes_gcm_decrypt(os.urandom(32), ciphertext, nonce)

    def test_tampered_ciphertext_rejected(self):
        key = os.urandom(32)
        ciphertext, nonce = aes_gcm_encrypt(key, b"data")
        tampered = bytes([b ^ 0xFF for b in ciphertext])
        with self.assertRaises(InvalidTag):
            aes_gcm_decrypt(key, tampered, nonce)

    def test_nonces_are_unique(self):
        key = os.urandom(32)
        nonces = {aes_gcm_encrypt(key, b"x")[1] for _ in range(100)}
        self.assertEqual(len(nonces), 100)


# ---------------------------------------------------------------------------
# 2. GervazyCryptoSession tests
# ---------------------------------------------------------------------------

class TestGervazyCryptoSession(TestCase):

    def setUp(self):
        self.user = User.objects.create_user("vault_user", password="pw")

    def test_initialize_strongbox_creates_models(self):
        session, wrapped_key = GervazyCryptoSession.initialize_strongbox(
            self.user, "test-strongbox-init", "pw"
        )
        self.assertTrue(UserStrongbox.objects.filter(name="test-strongbox-init").exists())
        self.assertTrue(VaultMasterKey.objects.filter(strongbox=session._strongbox).exists())
        self.assertTrue(WrappedDataKey.objects.filter(strongbox=session._strongbox).exists())

    def test_session_caches_keys_after_initialize(self):
        session, wrapped_key = GervazyCryptoSession.initialize_strongbox(
            self.user, "test-strongbox-cache", "pw"
        )
        self.assertIn(wrapped_key.vmk_id, session._vmk_cache)
        self.assertIn(wrapped_key.pk, session._dek_cache)

    def test_encrypt_and_decrypt_secret(self):
        session, wrapped_key = GervazyCryptoSession.initialize_strongbox(
            self.user, "test-strongbox-secret", "pw"
        )
        secret = session.encrypt_secret(wrapped_key, "my-api-key", name="api-key")
        result = session.decrypt_secret(secret)
        self.assertEqual(result, "my-api-key")

    def test_decrypt_secret_with_fresh_session(self):
        session1, wrapped_key = GervazyCryptoSession.initialize_strongbox(
            self.user, "test-strongbox-fresh", "pw123"
        )
        secret = session1.encrypt_secret(wrapped_key, "fresh-secret-value", name="test")

        # Open new session from DB.
        strongbox = session1._strongbox
        session2 = GervazyCryptoSession(strongbox, "pw123")
        self.assertEqual(session2.decrypt_secret(secret), "fresh-secret-value")

    def test_wrong_password_raises_on_decrypt(self):
        session, wrapped_key = GervazyCryptoSession.initialize_strongbox(
            self.user, "test-strongbox-badpw", "correct-password"
        )
        secret = session.encrypt_secret(wrapped_key, "value", name="x")

        strongbox = session._strongbox
        bad_session = GervazyCryptoSession(strongbox, "wrong-password")
        with self.assertRaises(InvalidTag):
            bad_session.decrypt_secret(secret)

    def test_encrypt_and_decrypt_private_key(self):
        session, wrapped_key = GervazyCryptoSession.initialize_strongbox(
            self.user, "test-strongbox-pk", "pw"
        )
        private_pem, public_pem = _make_rsa_pair()
        epk = session.encrypt_private_key(
            wrapped_key,
            private_pem,
            key_id="test-epk",
            key_type="RSA-2048",
            public_key_pem=public_pem,
        )
        result = session.decrypt_private_key(epk)
        self.assertEqual(result, private_pem)

    def test_derive_key_is_deterministic(self):
        strongbox = UserStrongbox.objects.create(owner=self.user, name="determ-strongbox")
        k1 = strongbox.derive_key("same-password")
        k2 = strongbox.derive_key("same-password")
        self.assertEqual(k1, k2)

    def test_derive_key_differs_for_different_passwords(self):
        strongbox = UserStrongbox.objects.create(owner=self.user, name="diff-strongbox")
        self.assertNotEqual(strongbox.derive_key("pw-a"), strongbox.derive_key("pw-b"))


# ---------------------------------------------------------------------------
# 3. SSO services tests
# ---------------------------------------------------------------------------

class _SSOServiceBase(TestCase):
    """Shared fixture: platform, RSA key pair, signing key via gervazy."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_pem, cls.public_pem = _make_rsa_pair()

    def setUp(self):
        self.admin = User.objects.create_superuser("admin", password="adminpw")
        self.user = User.objects.create_user(
            "alice", password="alicepw", email="alice@example.com",
            first_name="Alice", last_name="Smith",
        )
        self.platform = Platform.objects.create(
            site_name="Test Platform",
            domain="https://sso.example.com",
            author="Test",
            publication_year=2024,
            active=True,
        )
        self.signing_key, self.session, self.wrapped_key = _setup_sso_signing_key(
            self.admin, self.private_pem, self.public_pem
        )
        self.client_obj = SSORelyingParty.objects.create(
            client_id="relying-party-001",
            name="Test App",
            redirect_uris="https://app.example.com/callback/",
            allowed_scopes="openid email profile",
            trusted=True,
        )
        raw_secret = self.client_obj.set_client_secret("test-secret-xyz")
        self.client_secret = raw_secret
        self.client_obj.save()


@override_settings(SSO_VAULT_PASSWORD=_VAULT_PASSWORD)
class TestSSOServices(_SSOServiceBase):

    def test_get_issuer_from_platform(self):
        self.assertEqual(get_issuer(), "https://sso.example.com")

    def test_get_jwks_structure(self):
        jwks = get_jwks()
        self.assertIn("keys", jwks)
        self.assertEqual(len(jwks["keys"]), 1)
        key = jwks["keys"][0]
        for field in ("kty", "use", "kid", "alg", "n", "e"):
            self.assertIn(field, key)
        self.assertEqual(key["kty"], "RSA")
        self.assertEqual(key["alg"], "RS256")
        self.assertEqual(key["kid"], "test-sso-key-2026")

    def test_get_user_claims_openid_only(self):
        claims = get_user_claims(self.user, ["openid"])
        self.assertIn("sub", claims)
        self.assertNotIn("email", claims)
        self.assertNotIn("name", claims)

    def test_get_user_claims_email_scope(self):
        claims = get_user_claims(self.user, ["openid", "email"])
        self.assertEqual(claims["email"], "alice@example.com")
        self.assertTrue(claims["email_verified"])

    def test_get_user_claims_profile_scope(self):
        claims = get_user_claims(self.user, ["openid", "profile"])
        self.assertEqual(claims["preferred_username"], "alice")
        self.assertEqual(claims["given_name"], "Alice")
        self.assertEqual(claims["family_name"], "Smith")

    def test_subject_is_stable_across_calls(self):
        sub1 = get_user_claims(self.user, ["openid"])["sub"]
        sub2 = get_user_claims(self.user, ["openid"])["sub"]
        self.assertEqual(sub1, sub2)

    def test_build_id_token_valid_jwt(self):
        from unittest.mock import MagicMock
        request = MagicMock()
        request.build_absolute_uri.return_value = "https://sso.example.com/"
        token = build_id_token(request, user=self.user, client=self.client_obj,
                               scope="openid email", nonce="nonce-xyz")
        # Verify signature using the public key
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        public_key = load_pem_public_key(self.public_pem.encode())
        payload = jwt.decode(
            token, public_key, algorithms=["RS256"],
            audience="relying-party-001",
            options={"verify_iss": False},
        )
        self.assertEqual(payload["iss"], "https://sso.example.com")
        self.assertEqual(payload["aud"], "relying-party-001")
        self.assertEqual(payload["email"], "alice@example.com")
        self.assertEqual(payload["nonce"], "nonce-xyz")
        self.assertIn("sub", payload)
        self.assertGreater(payload["exp"], int(time.time()))

    def test_build_id_token_kid_in_header(self):
        from unittest.mock import MagicMock
        request = MagicMock()
        request.build_absolute_uri.return_value = "https://sso.example.com/"
        token = build_id_token(request, user=self.user, client=self.client_obj,
                               scope="openid")
        header = jwt.get_unverified_header(token)
        self.assertEqual(header["kid"], "test-sso-key-2026")

    def test_verify_pkce_s256(self):
        verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
        import hashlib
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        self.assertTrue(verify_pkce(verifier, challenge, "S256"))
        self.assertFalse(verify_pkce("wrong-verifier", challenge, "S256"))

    def test_verify_pkce_plain(self):
        self.assertTrue(verify_pkce("abc", "abc", "plain"))
        self.assertFalse(verify_pkce("abc", "xyz", "plain"))

    def test_verify_pkce_no_challenge_passes(self):
        self.assertTrue(verify_pkce(None, None, None))

    def test_verify_pkce_challenge_without_verifier_fails(self):
        self.assertFalse(verify_pkce(None, "some-challenge", "S256"))


# ---------------------------------------------------------------------------
# 4. SSO view tests
# ---------------------------------------------------------------------------

@override_settings(SSO_VAULT_PASSWORD=_VAULT_PASSWORD)
class TestSSOViews(_SSOServiceBase):

    def setUp(self):
        super().setUp()
        self.http = Client()

    # --- discovery & JWKS ---

    def test_openid_configuration_returns_json(self):
        resp = self.http.get("/.well-known/openid-configuration")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["issuer"], "https://sso.example.com")
        self.assertIn("authorization_endpoint", data)
        self.assertIn("token_endpoint", data)
        self.assertIn("jwks_uri", data)
        self.assertIn("userinfo_endpoint", data)

    def test_jwks_endpoint_returns_keys(self):
        resp = self.http.get(reverse("sso:jwks"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("keys", data)
        self.assertGreater(len(data["keys"]), 0)

    # --- login view ---

    def test_login_view_renders(self):
        resp = self.http.get(reverse("sso:login"))
        self.assertEqual(resp.status_code, 200)

    def test_login_view_valid_credentials(self):
        resp = self.http.post(
            reverse("sso:login"),
            {"username": "alice", "password": "alicepw"},
        )
        self.assertEqual(resp.status_code, 302)

    def test_login_view_invalid_credentials(self):
        resp = self.http.post(
            reverse("sso:login"),
            {"username": "alice", "password": "wrong"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'role="alert"')
        self.assertContains(resp, "Sign in failed")
        self.assertContains(resp, "Invalid username or password.")

    def test_login_view_invalid_form_shows_error(self):
        resp = self.http.post(
            reverse("sso:login"),
            {"username": "", "password": ""},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'role="alert"')
        self.assertContains(resp, "Sign in failed")
        self.assertContains(resp, "Enter your username and password.")
        self.assertContains(resp, "Try again in")

    @override_settings(LOGIN_RETRY_COOLDOWN_SECONDS=3)
    def test_login_view_cooldown_blocks_immediate_retry(self):
        self.http.post(
            reverse("sso:login"),
            {"username": "alice", "password": "wrong"},
        )
        resp = self.http.post(
            reverse("sso:login"),
            {"username": "alice", "password": "wrong"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Please wait")
        self.assertContains(resp, "Try again in")

    def test_login_view_preserves_next_on_failure(self):
        next_url = reverse("core:dashboard")
        resp = self.http.post(
            f"{reverse('sso:login')}?next={next_url}",
            {"username": "alice", "password": "wrong"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f'value="{next_url}"')

    # --- authorize ---

    def test_authorize_redirects_unauthenticated_to_login(self):
        url = (
            reverse("sso:authorize")
            + "?response_type=code&client_id=relying-party-001"
            "&redirect_uri=https://app.example.com/callback/"
            "&scope=openid email&state=xyz"
        )
        resp = self.http.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("sso/login", resp["Location"])

    def test_authorize_trusted_client_issues_code(self):
        self.http.force_login(self.user)
        url = (
            reverse("sso:authorize")
            + "?response_type=code&client_id=relying-party-001"
            "&redirect_uri=https://app.example.com/callback/"
            "&scope=openid email&state=csrf-state-42"
        )
        resp = self.http.get(url)
        self.assertEqual(resp.status_code, 302)
        location = resp["Location"]
        self.assertIn("code=", location)
        self.assertIn("state=csrf-state-42", location)

    def test_authorize_invalid_client_rejected(self):
        self.http.force_login(self.user)
        url = (
            reverse("sso:authorize")
            + "?response_type=code&client_id=nonexistent-client"
            "&redirect_uri=https://app.example.com/callback/&scope=openid"
        )
        resp = self.http.get(url)
        self.assertEqual(resp.status_code, 400)

    def test_authorize_bad_redirect_uri_rejected(self):
        self.http.force_login(self.user)
        url = (
            reverse("sso:authorize")
            + "?response_type=code&client_id=relying-party-001"
            "&redirect_uri=https://evil.example.com/callback/&scope=openid"
        )
        resp = self.http.get(url)
        self.assertEqual(resp.status_code, 400)

    def test_authorize_unsupported_response_type_rejected(self):
        self.http.force_login(self.user)
        url = (
            reverse("sso:authorize")
            + "?response_type=token&client_id=relying-party-001"
            "&redirect_uri=https://app.example.com/callback/&scope=openid"
        )
        resp = self.http.get(url)
        self.assertEqual(resp.status_code, 400)

    # --- token exchange ---

    def _get_auth_code(self, scope="openid email") -> str:
        self.http.force_login(self.user)
        url = (
            reverse("sso:authorize")
            + f"?response_type=code&client_id=relying-party-001"
            f"&redirect_uri=https://app.example.com/callback/"
            f"&scope={scope}&state=s"
        )
        resp = self.http.get(url)
        location = resp["Location"]
        code = dict(p.split("=") for p in location.split("?", 1)[1].split("&"))["code"]
        return code

    def test_token_endpoint_returns_tokens(self):
        code = self._get_auth_code()
        resp = self.http.post(reverse("sso:token"), {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://app.example.com/callback/",
            "client_id": "relying-party-001",
            "client_secret": self.client_secret,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("access_token", data)
        self.assertIn("id_token", data)
        self.assertEqual(data["token_type"], "Bearer")

    def test_token_endpoint_id_token_is_valid_jwt(self):
        code = self._get_auth_code(scope="openid email profile")
        resp = self.http.post(reverse("sso:token"), {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://app.example.com/callback/",
            "client_id": "relying-party-001",
            "client_secret": self.client_secret,
        })
        id_token = resp.json()["id_token"]
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        public_key = load_pem_public_key(self.public_pem.encode())
        payload = jwt.decode(
            id_token, public_key, algorithms=["RS256"],
            audience="relying-party-001",
            options={"verify_iss": False},
        )
        self.assertEqual(payload["email"], "alice@example.com")
        self.assertIn("sub", payload)

    def test_token_endpoint_rejects_used_code(self):
        code = self._get_auth_code()
        post_data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://app.example.com/callback/",
            "client_id": "relying-party-001",
            "client_secret": self.client_secret,
        }
        self.http.post(reverse("sso:token"), post_data)  # first use
        resp = self.http.post(reverse("sso:token"), post_data)  # second use
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "invalid_grant")

    def test_token_endpoint_rejects_wrong_secret(self):
        code = self._get_auth_code()
        resp = self.http.post(reverse("sso:token"), {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://app.example.com/callback/",
            "client_id": "relying-party-001",
            "client_secret": "wrong-secret",
        })
        self.assertEqual(resp.status_code, 401)

    def test_token_endpoint_rejects_wrong_redirect_uri(self):
        code = self._get_auth_code()
        resp = self.http.post(reverse("sso:token"), {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://evil.example.com/callback/",
            "client_id": "relying-party-001",
            "client_secret": self.client_secret,
        })
        self.assertEqual(resp.status_code, 400)

    # --- userinfo ---

    def test_userinfo_returns_claims(self):
        code = self._get_auth_code(scope="openid email profile")
        token_resp = self.http.post(reverse("sso:token"), {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://app.example.com/callback/",
            "client_id": "relying-party-001",
            "client_secret": self.client_secret,
        })
        access_token = token_resp.json()["access_token"]

        resp = self.http.get(
            reverse("sso:userinfo"),
            HTTP_AUTHORIZATION=f"Bearer {access_token}",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("sub", data)
        self.assertEqual(data["email"], "alice@example.com")
        self.assertEqual(data["preferred_username"], "alice")

    def test_userinfo_rejects_invalid_token(self):
        resp = self.http.get(
            reverse("sso:userinfo"),
            HTTP_AUTHORIZATION="Bearer invalid-token-xyz",
        )
        self.assertEqual(resp.status_code, 401)

    def test_userinfo_rejects_missing_bearer(self):
        resp = self.http.get(reverse("sso:userinfo"))
        self.assertEqual(resp.status_code, 401)


# ---------------------------------------------------------------------------
# 5. SSORelyingParty model and provisioning tests
# ---------------------------------------------------------------------------

class TestSSORelyingPartyModel(TestCase):

    def setUp(self):
        self.client_obj = SSORelyingParty(
            client_id="test-123",
            name="Test",
            redirect_uris="https://a.example.com/cb/\nhttps://b.example.com/cb/",
            allowed_scopes="openid email profile",
        )

    def test_redirect_uri_list(self):
        uris = self.client_obj.redirect_uri_list()
        self.assertEqual(len(uris), 2)
        self.assertIn("https://a.example.com/cb/", uris)

    def test_is_redirect_uri_allowed(self):
        self.assertTrue(self.client_obj.is_redirect_uri_allowed("https://a.example.com/cb/"))
        self.assertFalse(self.client_obj.is_redirect_uri_allowed("https://evil.example.com/"))

    def test_client_secret_hashed(self):
        raw = self.client_obj.set_client_secret("super-secret")
        self.assertEqual(raw, "super-secret")
        self.assertNotIn("super-secret", self.client_obj.client_secret_hash)
        self.assertTrue(self.client_obj.verify_client_secret("super-secret"))
        self.assertFalse(self.client_obj.verify_client_secret("wrong"))

    def test_public_client_no_secret_needed(self):
        self.client_obj.client_type = SSORelyingParty.PUBLIC
        self.assertTrue(self.client_obj.verify_client_secret(None))
        self.assertTrue(self.client_obj.verify_client_secret(""))

    def test_relying_party_uses_existing_client_table(self):
        self.assertTrue(issubclass(SSORelyingParty, SSOClient))

    def test_create_relying_party_provisions_confidential_credentials(self):
        provisioned = create_relying_party(
            name="Provisioned App",
            client_id="provisioned-rp",
            redirect_uris=["https://app.example.com/callback/"],
            raw_secret="secret-for-test",
        )
        relying_party = provisioned.relying_party
        self.assertEqual(relying_party.client_id, "provisioned-rp")
        self.assertEqual(provisioned.client_secret, "secret-for-test")
        self.assertTrue(relying_party.verify_client_secret("secret-for-test"))

    def test_create_relying_party_rejects_existing_client_id(self):
        create_relying_party(
            name="Provisioned App",
            client_id="duplicate-rp",
            redirect_uris=["https://app.example.com/callback/"],
            raw_secret="secret-for-test",
        )
        with self.assertRaises(RelyingPartyProvisioningError):
            create_relying_party(
                name="Duplicate App",
                client_id="duplicate-rp",
                redirect_uris=["https://app.example.com/callback/"],
            )
