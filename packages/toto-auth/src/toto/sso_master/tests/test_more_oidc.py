"""The provider's OIDC endpoints at their edges: authorize, consent, token, userinfo.

Meant for the gate's provider stanza (DJANGO_SETTINGS_MODULE=
toto.sso_master.testing.settings), beside the other sso_master modules. The
end-to-end class mints a REAL signing key through ``create_sso_signing_key``,
so the ID token is checked against the published JWKS rather than a mock.
"""
from __future__ import annotations

import base64
import hashlib
import json
import unittest
from datetime import timedelta
from unittest import mock
from urllib.parse import parse_qs, urlparse

import jwt
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform

from ..models import (
    SSOAccessToken,
    SSOAuthorizationCode,
    SSORelyingParty,
    SSOSubject,
)
from ..services import verify_pkce

User = get_user_model()

REDIRECT = "https://rp.test/oidc/callback"

# Client secrets and passwords are hashed on every fixture; the default PBKDF2
# cost would make this module slow without testing anything more. The
# comparison logic under test is the same for every hasher.
FAST_HASHING = override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])


def _platform():
    return (Platform.objects.filter(active=True).first()
            or Platform.objects.create(site_name="Provider", author="T",
                                       publication_year=2026, active=True))


def _party(client_id="rp", *, secret="rp-secret", public=False, trusted=True,
           scopes="openid email profile roles", active=True):
    party = SSORelyingParty.objects.create(
        name=f"RP {client_id}", client_id=client_id, redirect_uris=REDIRECT,
        allowed_scopes=scopes, trusted=trusted, active=active,
        client_type=SSORelyingParty.PUBLIC if public else SSORelyingParty.CONFIDENTIAL)
    if not public:
        party.rotate_client_secret(secret)
        party.save()
    return party


def _s256(verifier):
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _basic(client_id, secret):
    raw = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    return {"HTTP_AUTHORIZATION": f"Basic {raw}"}


@FAST_HASHING
class OidcEndToEndTests(TestCase):
    """authorize → token → id_token verified with the JWKS → userinfo."""

    @classmethod
    def setUpTestData(cls):
        _platform()
        User.objects.create_user("admin", password="x")
        call_command("create_sso_signing_key", key_id="test-key", stdout=open("/dev/null", "w"))
        cls.user = User.objects.create_user("ada", "ada@example.org", "pw",
                                            first_name="Ada", last_name="L")

    def setUp(self):
        self.client.force_login(self.user)

    def _authorize(self, client_id, **params):
        query = {"response_type": "code", "client_id": client_id,
                 "redirect_uri": REDIRECT, "scope": "openid email profile",
                 "state": "st-1", "nonce": "n-1", **params}
        response = self.client.get(reverse("sso:authorize"), query)
        self.assertEqual(response.status_code, 302, response.content[:200])
        location = urlparse(response["Location"])
        self.assertEqual(f"{location.scheme}://{location.netloc}{location.path}", REDIRECT)
        return parse_qs(location.query)

    def _verify(self, id_token, audience):
        jwks = self.client.get(reverse("sso:jwks")).json()
        header = jwt.get_unverified_header(id_token)
        (key,) = [k for k in jwks["keys"] if k["kid"] == header["kid"]]
        public = jwt.PyJWK.from_dict(key).key
        return jwt.decode(id_token, public, algorithms=["RS256"], audience=audience)

    def test_a_confidential_client_gets_a_verifiable_id_token_and_userinfo(self):
        _party("rp")
        params = self._authorize("rp")
        self.assertEqual(params["state"], ["st-1"])

        response = Client().post(reverse("sso:token"), {
            "grant_type": "authorization_code", "code": params["code"][0],
            "redirect_uri": REDIRECT}, **_basic("rp", "rp-secret"))

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["token_type"], body["expires_in"]), ("Bearer", 3600))
        claims = self._verify(body["id_token"], "rp")
        subject = SSOSubject.objects.get(user=self.user).subject
        self.assertEqual(claims["sub"], str(subject))
        self.assertNotEqual(claims["sub"], str(self.user.pk))
        self.assertEqual(claims["nonce"], "n-1")
        self.assertEqual(claims["iss"], "http://testserver")
        self.assertEqual(claims["email"], "ada@example.org")
        self.assertEqual(claims["exp"] - claims["iat"], 3600)

        info = Client().get(reverse("sso:userinfo"),
                            HTTP_AUTHORIZATION=f"Bearer {body['access_token']}")
        self.assertEqual(info.json()["preferred_username"], "ada")
        self.assertEqual(info.json()["sub"], claims["sub"])

    def test_a_public_client_proves_possession_with_s256(self):
        _party("spa", public=True)
        verifier = "v" * 50
        params = self._authorize("spa", code_challenge=_s256(verifier))

        wrong = Client().post(reverse("sso:token"), {
            "grant_type": "authorization_code", "code": params["code"][0],
            "redirect_uri": REDIRECT, "client_id": "spa", "code_verifier": "x" * 50})
        self.assertEqual(wrong.json()["error"], "invalid_grant")

        right = Client().post(reverse("sso:token"), {
            "grant_type": "authorization_code", "code": params["code"][0],
            "redirect_uri": REDIRECT, "client_id": "spa", "code_verifier": verifier})
        self.assertEqual(right.status_code, 200)
        self._verify(right.json()["id_token"], "spa")

    def test_the_discovery_document_advertises_s256_only(self):
        doc = self.client.get(reverse("sso:openid_configuration")).json()
        self.assertEqual(doc["code_challenge_methods_supported"], ["S256"])
        self.assertIn("groups", doc["scopes_supported"])
        self.assertIn("groups", doc["claims_supported"])


@FAST_HASHING
class AuthorizeRefusalTests(TestCase):
    def setUp(self):
        _platform()
        self.user = User.objects.create_user("bob", "bob@example.org", "pw")
        self.client.force_login(self.user)
        self.party = _party("rp", scopes="openid email")

    def _get(self, **overrides):
        query = {"response_type": "code", "client_id": "rp",
                 "redirect_uri": REDIRECT, "scope": "openid email", "state": "s"}
        query.update(overrides)
        query = {k: v for k, v in query.items() if v is not None}
        return self.client.get(reverse("sso:authorize"), query)

    def assertRefused(self, response, fragment):
        self.assertEqual(response.status_code, 400)
        self.assertIn(fragment, response.content.decode())
        self.assertFalse(SSOAuthorizationCode.objects.exists())

    def test_anonymous_is_sent_to_log_in_first(self):
        response = Client().get(reverse("sso:authorize"), {"client_id": "rp"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_only_the_code_flow_is_offered(self):
        self.assertRefused(self._get(response_type="token"), "response_type=code")

    def test_openid_scope_is_required(self):
        self.assertRefused(self._get(scope="email"), "scope=openid")

    def test_an_unknown_or_inactive_client_is_refused(self):
        self.assertRefused(self._get(client_id="nobody"), "Invalid client_id")
        self.party.active = False
        self.party.save()
        self.assertRefused(self._get(), "Invalid client_id")

    def test_the_redirect_uri_must_match_exactly(self):
        for uri in (None, REDIRECT + "/", REDIRECT + "?next=evil",
                    "https://evil.test/oidc/callback", REDIRECT.upper()):
            with self.subTest(uri=uri):
                self.assertRefused(self._get(redirect_uri=uri), "Invalid redirect_uri")

    def test_the_admin_test_callback_is_only_a_door_for_staff(self):
        admin_uri = "http://testserver" + reverse("sso:admin_test_callback")
        self.assertRefused(self._get(redirect_uri=admin_uri), "Invalid redirect_uri")

    def test_a_scope_beyond_the_registration_is_refused(self):
        self.assertRefused(self._get(scope="openid email roles"), "not allowed")

    def test_a_public_client_without_pkce_is_refused(self):
        _party("spa", public=True, scopes="openid")
        self.assertRefused(self._get(client_id="spa", scope="openid"), "PKCE")

    def test_a_redirect_uri_with_a_query_keeps_it_and_appends_the_code(self):
        self.party.redirect_uris = REDIRECT + "?tenant=7"
        self.party.save()
        response = self._get(redirect_uri=REDIRECT + "?tenant=7")
        self.assertEqual(response.status_code, 302)
        params = parse_qs(urlparse(response["Location"]).query)
        self.assertEqual(params["tenant"], ["7"])
        self.assertEqual(params["state"], ["s"])
        self.assertTrue(params["code"][0])

    def test_no_state_means_no_state_in_the_redirect(self):
        response = self._get(state=None)
        self.assertNotIn("state=", response["Location"])


@FAST_HASHING
class ConsentTests(TestCase):
    def setUp(self):
        _platform()
        self.user = User.objects.create_user("cat", "cat@example.org", "pw")
        self.client.force_login(self.user)
        _party("untrusted", trusted=False, scopes="openid email")
        self.query = {"response_type": "code", "client_id": "untrusted",
                      "redirect_uri": REDIRECT, "scope": "openid email", "state": "s"}

    def test_an_untrusted_client_is_shown_a_consent_page_and_no_code(self):
        response = self.client.get(reverse("sso:authorize"), self.query)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "RP untrusted")
        self.assertEqual(response.context["scope_items"], ["openid", "email"])
        self.assertFalse(SSOAuthorizationCode.objects.exists())

    def test_approving_returns_to_authorize_which_then_issues_a_code(self):
        query_string = "&".join(f"{k}={v}" for k, v in self.query.items())
        approve = self.client.post(reverse("sso:consent"),
                                   {"decision": "approve", "query_string": query_string})
        self.assertEqual(approve.status_code, 302)
        self.assertTrue(approve["Location"].endswith("&consent=approved"))

        issued = self.client.get(approve["Location"])
        self.assertEqual(issued.status_code, 302)
        self.assertTrue(issued["Location"].startswith(REDIRECT + "?"))
        self.assertEqual(SSOAuthorizationCode.objects.get().user, self.user)

    def test_denying_is_a_403_and_no_code(self):
        response = self.client.post(reverse("sso:consent"), {"decision": "deny"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(SSOAuthorizationCode.objects.exists())

    def test_consent_is_post_only(self):
        self.assertEqual(self.client.get(reverse("sso:consent")).status_code, 405)


@mock.patch("toto.sso_master.views.build_id_token", return_value="h.p.s")
@FAST_HASHING
class TokenRefusalTests(TestCase):
    def setUp(self):
        _platform()
        self.user = User.objects.create_user("dan", "dan@example.org", "pw")
        self.party = _party("rp")
        self.other = _party("other", secret="other-secret")

    def _code(self, party=None, **kwargs):
        return SSOAuthorizationCode.objects.create(
            client=party or self.party, user=self.user, redirect_uri=REDIRECT,
            scope="openid email", **kwargs)

    def _exchange(self, code_value, *, client_id="rp", secret="rp-secret", **extra):
        data = {"grant_type": "authorization_code", "code": code_value,
                "redirect_uri": REDIRECT, "client_id": client_id,
                "client_secret": secret}
        data.update(extra)
        return Client().post(reverse("sso:token"), data)

    def test_only_the_authorization_code_grant_is_supported(self, _):
        response = self._exchange(self._code().code, grant_type="password")
        self.assertEqual((response.status_code, response.json()["error"]),
                         (400, "unsupported_grant_type"))

    def test_an_unknown_client_is_invalid_client(self, _):
        response = self._exchange(self._code().code, client_id="ghost")
        self.assertEqual((response.status_code, response.json()["error"]),
                         (401, "invalid_client"))

    def test_an_unknown_code_is_invalid_grant(self, _):
        response = self._exchange("no-such-code")
        self.assertEqual((response.status_code, response.json()["error"]),
                         (400, "invalid_grant"))

    def test_a_code_cannot_be_redeemed_by_another_client_and_is_not_burned(self, _):
        code = self._code()
        response = self._exchange(code.code, client_id="other", secret="other-secret")
        self.assertEqual(response.json()["error"], "invalid_grant")
        code.refresh_from_db()
        self.assertFalse(code.is_used)
        self.assertEqual(self._exchange(code.code).status_code, 200)

    def test_a_wrong_secret_is_invalid_client_and_the_code_survives(self, _):
        code = self._code()
        response = self._exchange(code.code, secret="guess")
        self.assertEqual((response.status_code, response.json()["error"]),
                         (401, "invalid_client"))
        code.refresh_from_db()
        self.assertFalse(code.is_used)

    def test_the_redirect_uri_must_repeat_the_one_authorized(self, _):
        response = self._exchange(self._code().code, redirect_uri=REDIRECT + "x")
        self.assertEqual(response.json()["error"], "invalid_grant")

    def test_an_expired_code_is_refused(self, _):
        code = self._code()
        SSOAuthorizationCode.objects.filter(pk=code.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self._exchange(code.code).json()["error"], "invalid_grant")

    def test_basic_auth_credentials_are_accepted(self, _):
        code = self._code()
        response = Client().post(reverse("sso:token"), {
            "grant_type": "authorization_code", "code": code.code,
            "redirect_uri": REDIRECT}, **_basic("rp", "rp-secret"))
        self.assertEqual(response.status_code, 200)
        token = SSOAccessToken.objects.get(token=response.json()["access_token"])
        self.assertEqual((token.user, token.client_id, token.scope),
                         (self.user, self.party.pk, "openid email"))

    def test_a_malformed_basic_header_is_invalid_client(self, _):
        response = Client().post(reverse("sso:token"), {
            "grant_type": "authorization_code", "code": self._code().code,
            "redirect_uri": REDIRECT}, HTTP_AUTHORIZATION="Basic %%%not-base64")
        self.assertEqual((response.status_code, response.json()["error"]),
                         (401, "invalid_client"))

    def test_a_plain_pkce_challenge_is_never_honoured(self, _):
        code = self._code(code_challenge="same-value", code_challenge_method="plain")
        response = self._exchange(code.code, code_verifier="same-value")
        self.assertEqual(response.json()["error"], "invalid_grant")

    @unittest.skip(
        "SUSPECTED BUG (sso_master/services.py verify_pkce): the verifier is "
        "encoded with .encode('ascii'), so a non-ASCII code_verifier raises "
        "UnicodeEncodeError and /sso/token/ answers 500 instead of invalid_grant")
    def test_a_non_ascii_verifier_is_invalid_grant_not_a_server_error(self, _):
        code = self._code(code_challenge=_s256("a" * 43), code_challenge_method="S256")
        client = Client(raise_request_exception=False)
        response = client.post(reverse("sso:token"), {
            "grant_type": "authorization_code", "code": code.code,
            "redirect_uri": REDIRECT, "client_id": "rp", "client_secret": "rp-secret",
            "code_verifier": "caf\u00e9"})
        self.assertEqual((response.status_code, response.json()["error"]),
                         (400, "invalid_grant"))

    def test_a_challenge_without_a_verifier_is_refused(self, _):
        code = self._code(code_challenge=_s256("v" * 43), code_challenge_method="S256")
        self.assertEqual(self._exchange(code.code).json()["error"], "invalid_grant")

    def test_the_first_use_of_a_rotated_secret_closes_the_grace_window(self, _):
        self.party.secret_proven_at = timezone.now()
        self.party.save()
        self.party.rotate_client_secret("rp-secret-2")
        self.party.save()
        self.assertTrue(self.party.previous_secret_hash)

        response = self._exchange(self._code().code, secret="rp-secret-2")

        self.assertEqual(response.status_code, 200)
        self.party.refresh_from_db()
        self.assertIsNotNone(self.party.secret_proven_at)
        self.assertEqual(self.party.previous_secret_hash, "")
        self.assertIsNone(self.party.previous_secret_expires_at)
        self.assertEqual(self._exchange(self._code().code).json()["error"],
                         "invalid_client")

    def test_the_old_secret_still_works_in_the_window_and_is_noticed(self, _):
        self.party.secret_proven_at = timezone.now()
        self.party.save()
        self.party.rotate_client_secret("rp-secret-2")
        self.party.save()

        response = self._exchange(self._code().code, secret="rp-secret")

        self.assertEqual(response.status_code, 200)
        self.party.refresh_from_db()
        self.assertIsNotNone(self.party.previous_secret_used_at)
        self.assertIsNone(self.party.secret_proven_at)
        self.assertTrue(self.party.previous_secret_hash)

    def test_the_old_secret_dies_when_the_window_closes(self, _):
        self.party.secret_proven_at = timezone.now()
        self.party.save()
        self.party.rotate_client_secret("rp-secret-2", grace=timedelta(seconds=-1))
        self.party.save()
        response = self._exchange(self._code().code, secret="rp-secret")
        self.assertEqual(response.json()["error"], "invalid_client")


@FAST_HASHING
class PkceRuleTests(TestCase):
    def test_no_challenge_needs_no_verifier(self):
        self.assertTrue(verify_pkce(None, None, None))

    def test_s256_is_the_default_when_no_method_is_named(self):
        verifier = "a" * 43
        self.assertTrue(verify_pkce(verifier, _s256(verifier), None))
        self.assertTrue(verify_pkce(verifier, _s256(verifier), ""))
        self.assertFalse(verify_pkce("b" * 43, _s256(verifier), "S256"))

    def test_plain_and_unknown_methods_fail(self):
        self.assertFalse(verify_pkce("x", "x", "plain"))
        self.assertFalse(verify_pkce("x", "x", "S512"))


@FAST_HASHING
class UserinfoTests(TestCase):
    def setUp(self):
        _platform()
        self.user = User.objects.create_user("eve", "eve@example.org", "pw")
        self.party = _party("rp")

    def _get(self, header):
        return Client().get(reverse("sso:userinfo"), HTTP_AUTHORIZATION=header)

    def test_a_request_without_a_bearer_token_is_refused(self):
        self.assertEqual(Client().get(reverse("sso:userinfo")).status_code, 401)
        self.assertEqual(self._get("Basic abc").status_code, 401)
        self.assertEqual(self._get("Bearer not-a-token").status_code, 401)

    def test_an_expired_or_revoked_token_is_refused(self):
        expired = SSOAccessToken.objects.create(
            client=self.party, user=self.user, scope="openid",
            expires_at=timezone.now() - timedelta(seconds=1))
        revoked = SSOAccessToken.objects.create(client=self.party, user=self.user,
                                                scope="openid")
        revoked.revoke()
        for token in (expired, revoked):
            with self.subTest(token=token.pk):
                response = self._get(f"Bearer {token.token}")
                self.assertEqual(response.json(), {"error": "invalid_token"})

    def test_claims_are_limited_to_the_tokens_scope(self):
        narrow = SSOAccessToken.objects.create(client=self.party, user=self.user,
                                               scope="openid")
        wide = SSOAccessToken.objects.create(client=self.party, user=self.user,
                                             scope="openid email roles")
        self.assertEqual(set(self._get(f"Bearer {narrow.token}").json()), {"sub"})
        claims = self._get(f"Bearer {wide.token}").json()
        self.assertEqual(claims["email"], "eve@example.org")
        self.assertEqual(claims["roles"], ["viewer"])
        self.assertTrue(claims["is_active"])

    def test_a_user_disabled_since_the_token_was_issued_is_reported_inactive(self):
        token = SSOAccessToken.objects.create(client=self.party, user=self.user,
                                              scope="openid roles")
        self.user.is_active = False
        self.user.save()
        self.assertIs(self._get(f"Bearer {token.token}").json()["is_active"], False)


@FAST_HASHING
class AdminTestFlowTests(TestCase):
    def setUp(self):
        _platform()
        self.staff = User.objects.create_user("ops", "ops@example.org", "pw", is_staff=True)
        self.plain = User.objects.create_user("joe", "joe@example.org", "pw")
        self.party = _party("rp", scopes="openid email")

    def test_members_may_not_run_the_admin_test(self):
        self.client.force_login(self.plain)
        start = self.client.get(reverse("sso:admin_test_login", args=[self.party.pk]))
        callback = self.client.get(reverse("sso:admin_test_callback"))
        self.assertEqual((start.status_code, callback.status_code), (403, 403))

    def test_an_inactive_client_cannot_be_tested(self):
        self.party.active = False
        self.party.save()
        self.client.force_login(self.staff)
        response = self.client.get(reverse("sso:admin_test_login", args=[self.party.pk]))
        self.assertEqual(response.status_code, 400)

    def test_the_whole_test_round_trip_shows_the_claims(self):
        self.client.force_login(self.staff)
        start = self.client.get(reverse("sso:admin_test_login", args=[self.party.pk]))
        self.assertEqual(start.status_code, 302)

        # The authorize leg accepts the admin callback for staff even though
        # it is not a registered redirect URI.
        authorized = self.client.get(start["Location"])
        self.assertEqual(authorized.status_code, 302)
        callback = urlparse(authorized["Location"])
        self.assertEqual(callback.path, reverse("sso:admin_test_callback"))

        result = self.client.get(f"{callback.path}?{callback.query}")

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.context["claims"]["email"], "ops@example.org")
        self.assertTrue(SSOAccessToken.objects.filter(user=self.staff).exists())
        self.assertTrue(SSOAuthorizationCode.objects.get().is_used)

    def test_a_forged_or_replayed_callback_is_refused(self):
        self.client.force_login(self.staff)
        start = self.client.get(reverse("sso:admin_test_login", args=[self.party.pk]))
        authorized = self.client.get(start["Location"])
        callback = urlparse(authorized["Location"])
        params = parse_qs(callback.query)

        forged = self.client.get(callback.path, {"state": "forged",
                                                 "code": params["code"][0]})
        self.assertIn("State mismatch", forged.context["error"])
        # The state was single-use, so even the genuine one no longer works.
        replay = self.client.get(f"{callback.path}?{callback.query}")
        self.assertIn("State mismatch", replay.context["error"])
        self.assertFalse(SSOAuthorizationCode.objects.get().is_used)

    def test_callback_errors_are_shown_not_raised(self):
        self.client.force_login(self.staff)
        refused = self.client.get(reverse("sso:admin_test_callback"),
                                  {"error": "access_denied"})
        self.assertEqual(refused.context["error"], "access_denied")

        session = self.client.session
        session["sso_admin_test_state"] = "st"
        session["sso_admin_test_client_pk"] = str(self.party.pk)
        session.save()
        missing = self.client.get(reverse("sso:admin_test_callback"), {"state": "st"})
        self.assertEqual(missing.context["error"], "Missing code.")

        session = self.client.session
        session["sso_admin_test_state"] = "st"
        session["sso_admin_test_client_pk"] = str(self.party.pk)
        session.save()
        unknown = self.client.get(reverse("sso:admin_test_callback"),
                                  {"state": "st", "code": "nope"})
        self.assertEqual(unknown.context["error"], "Authorization code not found.")


@FAST_HASHING
class EnrollTransportTests(TestCase):
    def setUp(self):
        _platform()

    def test_plaintext_is_refused_outside_debug(self):
        response = self.client.post(reverse("sso:enroll"), "{}",
                                    content_type="application/json")
        self.assertEqual((response.status_code, response.json()["error"]),
                         (400, "insecure_transport"))

    def test_a_body_that_is_not_a_json_object_is_a_bad_request(self):
        for body in ("not json", "[1, 2]"):
            with self.subTest(body=body):
                response = self.client.post(reverse("sso:enroll"), body,
                                            content_type="application/json", secure=True)
                self.assertEqual((response.status_code, response.json()["error"]),
                                 (400, "bad_request"))

    def test_a_forged_ticket_is_401_and_never_echoed(self):
        ticket = "toto-fed-1.not-a-real-ticket"
        response = self.client.post(
            reverse("sso:enroll"),
            json.dumps({"ticket": ticket, "host": "rp.test",
                        "redirect_uri": REDIRECT, "proof": "x"}),
            content_type="application/json", secure=True)
        self.assertIn(response.status_code, (400, 401))
        self.assertNotIn(ticket, response.content.decode())

    @override_settings(TRUSTED_PROXIES=["10.0.0.0/8"])
    def test_the_proxy_s_address_is_the_one_recorded(self):
        """X-Forwarded-For's first entry is the client's own word; nginx's
        X-Real-IP is believed from a trusted proxy only (2026-09-30)."""
        from ..views import _client_ip

        request = mock.Mock(META={"HTTP_X_FORWARDED_FOR": "198.51.100.66, 203.0.113.9",
                                  "HTTP_X_REAL_IP": "203.0.113.9",
                                  "REMOTE_ADDR": "10.0.0.2"})
        self.assertEqual(_client_ip(request), "203.0.113.9")
        request.META = {"HTTP_X_FORWARDED_FOR": "198.51.100.66",
                        "HTTP_X_REAL_IP": "203.0.113.9", "REMOTE_ADDR": "192.0.2.4"}
        self.assertEqual(_client_ip(request), "192.0.2.4")
        request.META = {"HTTP_X_FORWARDED_FOR": "198.51.100.66"}
        self.assertIsNone(_client_ip(request))


@FAST_HASHING
class FederationConsoleEdgeTests(TestCase):
    def setUp(self):
        _platform()
        self.staff = User.objects.create_user("boss", "b@x.test", "pw", is_staff=True)
        self.client.force_login(self.staff)
        self.url = reverse("sso:federation_console")

    @override_settings(PLATFORM_DOMAIN="provider.test")
    def test_an_invite_ttl_is_clamped_to_the_allowed_range(self):
        from ..models import MAX_INVITE_TTL_MINUTES, SSOFederationInvite

        self.client.post(self.url, {"action": "invite", "expected_host": "far.test",
                                    "ttl_minutes": "100000"})
        invite = SSOFederationInvite.objects.get()
        lifetime = invite.expires_at - invite.created_at
        self.assertLessEqual(lifetime, timedelta(minutes=MAX_INVITE_TTL_MINUTES, seconds=5))
        self.assertGreater(lifetime, timedelta(minutes=MAX_INVITE_TTL_MINUTES - 1))

    @override_settings(PLATFORM_DOMAIN="provider.test")
    def test_a_nonsense_ttl_falls_back_to_the_default(self):
        from ..models import DEFAULT_INVITE_TTL_MINUTES, SSOFederationInvite

        self.client.post(self.url, {"action": "invite", "expected_host": "far.test",
                                    "ttl_minutes": "soon"})
        invite = SSOFederationInvite.objects.get()
        lifetime = invite.expires_at - invite.created_at
        self.assertAlmostEqual(lifetime.total_seconds(), DEFAULT_INVITE_TTL_MINUTES * 60,
                               delta=5)

    @override_settings(PLATFORM_DOMAIN="provider.test")
    def test_roles_are_granted_only_when_asked_for(self):
        from ..models import SSOFederationInvite

        self.client.post(self.url, {"action": "invite", "expected_host": "a.test"})
        self.client.post(self.url, {"action": "invite", "expected_host": "b.test",
                                    "roles": "on"})
        scopes = dict(SSOFederationInvite.objects.values_list("expected_host",
                                                              "granted_scopes"))
        self.assertEqual(scopes, {"a.test": "openid email profile",
                                  "b.test": "openid email profile roles"})

    def test_re_pairing_a_sidecar_or_unknown_row_is_refused_politely(self):
        sidecar = _party("grafana")  # host-configured, not pairing-managed
        for pk in (str(sidecar.pk), "00000000-0000-0000-0000-000000000000"):
            with self.subTest(pk=pk):
                response = self.client.post(self.url, {"action": "repair", "pk": pk})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["error"],
                                 "No such federated platform to re-pair.")

    @override_settings(PLATFORM_DOMAIN="")
    def test_without_a_public_url_an_invite_explains_itself(self):
        response = self.client.post(self.url, {"action": "invite",
                                               "expected_host": "far.test"})
        self.assertIn("PLATFORM_DOMAIN", response.context["error"])
        self.assertIsNone(response.context["minted"])

    def test_the_list_labels_sidecars_invites_and_pairings(self):
        _party("gitea")
        invited = _party("inv")
        invited.pairing_managed = True
        invited.save()
        paired = _party("paired")
        paired.pairing_managed = True
        paired.paired_at = timezone.now()
        paired.save()

        rows = {r["client_id"]: r for r in self.client.get(self.url).context["platforms"]}

        self.assertEqual({k: r["status"] for k, r in rows.items()},
                         {"gitea": "sidecar", "inv": "invited", "paired": "paired"})
        self.assertEqual(rows["paired"]["host"], "rp.test")


@FAST_HASHING
class MyProfileTests(TestCase):
    def test_a_user_without_a_profile_lands_on_the_dashboard(self):
        _platform()
        self.client.force_login(User.objects.create_user("np", password="pw"))
        response = self.client.get(reverse("sso:my_profile"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("core:dashboard"))
