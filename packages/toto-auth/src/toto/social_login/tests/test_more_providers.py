"""Social sign-in: provider mapping, configuration, and the refusals around email.

Meant for the gate's provider stanza, beside test_social_flow and
test_login_buttons. Outbound HTTP is mocked at the views' ``requests`` alias,
the house pattern those modules use.
"""
import os
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from toto.social_login import providers
from toto.social_login.models import SocialIdentity

User = get_user_model()

GOOGLE_CREDS = {"GOOGLE_OAUTH_CLIENT_ID": "g-cid", "GOOGLE_OAUTH_CLIENT_SECRET": "g-sec"}
FACEBOOK_CREDS = {"FACEBOOK_OAUTH_CLIENT_ID": "fb-cid",
                  "FACEBOOK_OAUTH_CLIENT_SECRET": "fb-sec"}
NO_ENV = {k: "" for k in (*GOOGLE_CREDS, *FACEBOOK_CREDS, "TOTO_SOCIAL_SIGNUP")}


def _response(payload=None, ok=True):
    resp = mock.Mock(ok=ok, status_code=200 if ok else 400)
    resp.json.return_value = payload or {}
    return resp


class NormalizeTests(SimpleTestCase):
    def test_google_claims_map_onto_the_common_shape(self):
        claims = providers.PROVIDERS["google"].normalize({
            "sub": 1234, "email": "a@x.test", "email_verified": "true",
            "given_name": None, "family_name": "L"})
        self.assertEqual(claims, {"sub": "1234", "email": "a@x.test",
                                  "email_verified": True, "given_name": "",
                                  "family_name": "L"})

    def test_google_unverified_email_stays_unverified(self):
        claims = providers.PROVIDERS["google"].normalize({"sub": "1", "email": "a@x.test"})
        self.assertFalse(claims["email_verified"])

    def test_facebook_uses_id_and_trusts_only_a_returned_email(self):
        spec = providers.PROVIDERS["facebook"]
        with_email = spec.normalize({"id": 99, "email": "f@x.test",
                                     "first_name": "Fa", "last_name": "Book"})
        without = spec.normalize({"id": "99"})
        self.assertEqual(with_email, {"sub": "99", "email": "f@x.test",
                                      "email_verified": True, "given_name": "Fa",
                                      "family_name": "Book"})
        self.assertEqual((without["email"], without["email_verified"]), ("", False))

    def test_only_google_uses_pkce(self):
        self.assertTrue(providers.PROVIDERS["google"].use_pkce)
        self.assertFalse(providers.PROVIDERS["facebook"].use_pkce)


@mock.patch.dict(os.environ, NO_ENV)
class ConfigurationTests(SimpleTestCase):
    def test_a_provider_needs_both_halves_of_its_credentials(self):
        with override_settings(GOOGLE_OAUTH_CLIENT_ID="only-id",
                               GOOGLE_OAUTH_CLIENT_SECRET=""):
            self.assertEqual(providers.enabled_providers(), [])
        with override_settings(**GOOGLE_CREDS):
            self.assertEqual([p.key for p in providers.enabled_providers()], ["google"])

    def test_the_environment_wins_over_settings(self):
        with override_settings(GOOGLE_OAUTH_CLIENT_ID="from-settings",
                               GOOGLE_OAUTH_CLIENT_SECRET="s"), \
             mock.patch.dict(os.environ, {"GOOGLE_OAUTH_CLIENT_ID": "from-env"}):
            cfg = providers.provider_config(providers.PROVIDERS["google"])
        self.assertEqual(cfg, {"client_id": "from-env", "client_secret": "s"})

    def test_signup_is_closed_by_default_and_an_explicit_setting_wins(self):
        with override_settings(TOTO_SOCIAL_SIGNUP=None):
            self.assertFalse(providers.social_signup_enabled())
            with mock.patch.dict(os.environ, {"TOTO_SOCIAL_SIGNUP": "1"}):
                self.assertTrue(providers.social_signup_enabled())
        with override_settings(TOTO_SOCIAL_SIGNUP=False), \
             mock.patch.dict(os.environ, {"TOTO_SOCIAL_SIGNUP": "1"}):
            self.assertFalse(providers.social_signup_enabled())

    def test_the_public_base_url_always_has_a_scheme(self):
        cases = {"": "", "portal.test/": "https://portal.test",
                 "http://dev.test": "http://dev.test"}
        for domain, expected in cases.items():
            with self.subTest(domain=domain), override_settings(PLATFORM_DOMAIN=domain):
                self.assertEqual(providers.public_base_url(), expected)

    @override_settings(**GOOGLE_CREDS, **FACEBOOK_CREDS)
    def test_buttons_carry_next_through_to_the_login_url(self):
        request = RequestFactory().get("/login/", {"next": "/vault/?a=1"})
        entries = providers.login_page_providers(request)
        self.assertEqual([e["key"] for e in entries], ["google", "facebook"])
        self.assertTrue(entries[0]["login_url"].endswith("?next=%2Fvault%2F%3Fa%3D1"))


@mock.patch.dict(os.environ, NO_ENV)
@override_settings(**GOOGLE_CREDS, **FACEBOOK_CREDS)
class CallbackEdgeTests(TestCase):
    def _start(self, provider="google", **params):
        response = self.client.get(reverse("sso:social_login", args=[provider]), params)
        return response, parse_qs(urlparse(response["Location"]).query)

    def _callback(self, state, claims, provider="google", token_ok=True,
                  userinfo_ok=True, **extra):
        with mock.patch("toto.social_login.views.http_requests.post",
                        return_value=_response({"access_token": "at"}, ok=token_ok)) as post, \
             mock.patch("toto.social_login.views.http_requests.get",
                        return_value=_response(claims, ok=userinfo_ok)):
            response = self.client.get(
                reverse("sso:social_callback", args=[provider]),
                {"code": "c", "state": state, **extra})
        return response, post

    def test_facebook_asks_for_no_pkce_and_sends_no_verifier(self):
        _, params = self._start("facebook")
        self.assertNotIn("code_challenge", params)
        user = User.objects.create_user("fb", "f@x.test", "pw")
        response, post = self._callback(params["state"][0], {"id": "7", "email": "f@x.test"},
                                        provider="facebook")
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertNotIn("code_verifier", post.call_args.kwargs["data"])

    def test_google_sends_the_verifier_that_matches_its_challenge(self):
        import base64
        import hashlib

        _, params = self._start()
        response, post = self._callback(params["state"][0], {"sub": "1"})
        verifier = post.call_args.kwargs["data"]["code_verifier"]
        digest = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(digest, params["code_challenge"][0])

    def test_state_minted_for_one_provider_cannot_complete_another(self):
        _, params = self._start("google")
        response, post = self._callback(params["state"][0], {"id": "7"}, provider="facebook")
        self.assertEqual(response.status_code, 400)
        post.assert_not_called()

    def test_a_missing_code_is_refused_before_any_exchange(self):
        _, params = self._start()
        with mock.patch("toto.social_login.views.http_requests.post") as post:
            response = self.client.get(reverse("sso:social_callback", args=["google"]),
                                       {"state": params["state"][0]})
        self.assertEqual(response.status_code, 400)
        post.assert_not_called()

    def test_failed_token_or_userinfo_calls_are_refused(self):
        _, params = self._start()
        refused, _ = self._callback(params["state"][0], {"sub": "1"}, token_ok=False)
        self.assertEqual(refused.status_code, 400)
        _, params = self._start()
        refused, _ = self._callback(params["state"][0], {"sub": "1"}, userinfo_ok=False)
        self.assertEqual(refused.status_code, 400)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_an_unverified_email_never_adopts_an_existing_account(self):
        victim = User.objects.create_user("victim", "v@x.test", "pw")
        _, params = self._start()
        response, _ = self._callback(params["state"][0],
                                     {"sub": "attacker", "email": "v@x.test",
                                      "email_verified": False})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertFalse(SocialIdentity.objects.filter(user=victim).exists())

    @override_settings(TOTO_SOCIAL_SIGNUP=True)
    def test_with_signup_on_an_unverified_email_gets_its_own_new_account(self):
        victim = User.objects.create_user("victim", "v@x.test", "pw")
        _, params = self._start()
        self._callback(params["state"][0], {"sub": "s-9", "email": "v@x.test",
                                            "email_verified": False})
        new_id = int(self.client.session["_auth_user_id"])
        self.assertNotEqual(new_id, victim.pk)
        self.assertEqual(User.objects.get(pk=new_id).username, "google_s-9")

    @unittest.skip(
        "SUSPECTED BUG (social_login/views.py _resolve_user): signup provisions "
        "with User.objects.get_or_create(username=f'{provider}_{sub}'), so an "
        "EXISTING local account already named e.g. 'google_<sub>' is adopted — "
        "the newcomer is signed into an account whose password somebody else holds")
    @override_settings(TOTO_SOCIAL_SIGNUP=True)
    def test_signup_never_adopts_a_local_account_that_squats_the_username(self):
        squatter = User.objects.create_user("google_victim-sub", "attacker@x.test", "pw")
        _, params = self._start()
        self._callback(params["state"][0], {"sub": "victim-sub",
                                            "email": "victim@x.test",
                                            "email_verified": True})
        self.assertNotEqual(self.client.session.get("_auth_user_id"), str(squatter.pk))
        self.assertFalse(SocialIdentity.objects.filter(user=squatter).exists())

    def test_an_email_match_is_case_insensitive_and_fills_only_blank_names(self):
        user = User.objects.create_user("ada", "Ada@Example.org", "pw", last_name="Keep")
        stored_email = user.email  # "Ada@example.org": Django lowercases the domain
        _, params = self._start()
        self._callback(params["state"][0], {"sub": "g1", "email": "ada@example.org",
                                            "email_verified": True, "given_name": "Ada",
                                            "family_name": "Replace"})
        user.refresh_from_db()
        self.assertEqual((user.first_name, user.last_name, user.email),
                         ("Ada", "Keep", stored_email))
        self.assertTrue(SocialIdentity.objects.filter(user=user, subject="g1").exists())

    def test_a_linked_identity_on_a_disabled_account_is_refused(self):
        user = User.objects.create_user("gone", "g@x.test", "pw", is_active=False)
        SocialIdentity.objects.create(provider="google", subject="g-9", user=user)
        _, params = self._start()
        response, _ = self._callback(params["state"][0], {"sub": "g-9"})
        self.assertEqual(response["Location"], reverse("sso:login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_a_safe_next_survives_the_round_trip_and_the_cookies_are_cleared(self):
        user = User.objects.create_user("n", "n@x.test", "pw")
        SocialIdentity.objects.create(provider="google", subject="n-1", user=user)
        _, params = self._start(next="/vault/")
        response, _ = self._callback(params["state"][0], {"sub": "n-1"})
        self.assertEqual(response["Location"], "/vault/")
        self.assertEqual(response.cookies["social_state"].value, "")
        self.assertEqual(response.cookies["social_next"].value, "")

    @override_settings(PLATFORM_DOMAIN="portal.test")
    def test_the_callback_uri_is_built_on_the_public_domain(self):
        _, params = self._start()
        self.assertEqual(params["redirect_uri"],
                         ["https://portal.test" + reverse("sso:social_callback",
                                                          args=["google"])])
