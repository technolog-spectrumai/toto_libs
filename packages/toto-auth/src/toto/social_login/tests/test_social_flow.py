"""OAuth flow tests: state handling, the four-step account resolution, and
the TOTO_SOCIAL_SIGNUP gate. Outbound HTTP is mocked at the views' requests
alias (house pattern — no mocking library)."""
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.social_login.models import SocialIdentity

User = get_user_model()

GOOGLE_CREDS = {"GOOGLE_OAUTH_CLIENT_ID": "test-cid",
                "GOOGLE_OAUTH_CLIENT_SECRET": "test-secret"}
FACEBOOK_CREDS = {"FACEBOOK_OAUTH_CLIENT_ID": "fb-cid",
                  "FACEBOOK_OAUTH_CLIENT_SECRET": "fb-secret"}


def _token_response(ok=True):
    resp = mock.Mock()
    resp.ok = ok
    resp.status_code = 200 if ok else 400
    resp.json.return_value = {"access_token": "at-123"}
    return resp


def _userinfo_response(payload, ok=True):
    resp = mock.Mock()
    resp.ok = ok
    resp.status_code = 200 if ok else 400
    resp.json.return_value = payload
    return resp


GOOGLE_CLAIMS = {"sub": "g-sub-1", "email": "ada@example.org",
                 "email_verified": True, "given_name": "Ada", "family_name": "L"}


@override_settings(**GOOGLE_CREDS)
class SocialLoginRedirectTests(TestCase):
    def test_login_redirects_to_provider_with_state_and_pkce(self):
        response = self.client.get(reverse("sso:social_login", args=["google"]))
        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.netloc, "accounts.google.com")
        params = parse_qs(parsed.query)
        self.assertTrue(params["state"][0])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertIn("social_state", response.cookies)

    def test_unknown_provider_404(self):
        response = self.client.get(reverse("sso:social_login", args=["github"]))
        self.assertEqual(response.status_code, 404)

    def test_unconfigured_provider_404(self):
        response = self.client.get(reverse("sso:social_login", args=["facebook"]))
        self.assertEqual(response.status_code, 404)

    def test_hostile_next_is_dropped(self):
        response = self.client.get(reverse("sso:social_login", args=["google"]),
                                   {"next": "https://evil.example.com/"})
        self.assertNotIn("social_next", response.cookies)


@override_settings(**GOOGLE_CREDS)
class SocialCallbackTests(TestCase):
    def _start(self, provider="google"):
        """Begin the flow honestly: capture the state the login view minted."""
        response = self.client.get(reverse("sso:social_login", args=[provider]))
        params = parse_qs(urlparse(response["Location"]).query)
        return params["state"][0]

    def _callback(self, state, provider="google", claims=None, **extra):
        with mock.patch("toto.social_login.views.http_requests.post",
                        return_value=_token_response()), \
             mock.patch("toto.social_login.views.http_requests.get",
                        return_value=_userinfo_response(claims or dict(GOOGLE_CLAIMS))):
            return self.client.get(reverse("sso:social_callback", args=[provider]),
                                   {"code": "auth-code", "state": state, **extra})

    def test_state_mismatch_is_rejected(self):
        self._start()
        response = self.client.get(reverse("sso:social_callback", args=["google"]),
                                   {"code": "auth-code", "state": "forged"})
        self.assertEqual(response.status_code, 400)

    def test_missing_state_cookie_is_rejected(self):
        response = self.client.get(reverse("sso:social_callback", args=["google"]),
                                   {"code": "auth-code", "state": "whatever"})
        self.assertEqual(response.status_code, 400)

    def test_existing_identity_logs_in(self):
        user = User.objects.create_user("ada", "old@example.org", "pw")
        SocialIdentity.objects.create(provider="google", subject="g-sub-1",
                                      user=user, email="old@example.org")
        state = self._start()
        response = self._callback(state)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("core:dashboard"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        identity = SocialIdentity.objects.get(provider="google", subject="g-sub-1")
        self.assertEqual(identity.email, "ada@example.org")  # refreshed

    def test_verified_email_match_links_identity(self):
        user = User.objects.create_user("ada", "ada@example.org", "pw")
        state = self._start()
        response = self._callback(state)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertTrue(SocialIdentity.objects.filter(provider="google",
                                                      subject="g-sub-1",
                                                      user=user).exists())
        self.assertEqual(User.objects.count(), 1)  # no new user

    def test_unmatched_with_signup_off_is_rejected(self):
        state = self._start()
        response = self._callback(state)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("sso:login"))
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(SocialIdentity.objects.count(), 0)

    @override_settings(TOTO_SOCIAL_SIGNUP=True)
    def test_unmatched_with_signup_on_provisions_user(self):
        state = self._start()
        response = self._callback(state)
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username="google_g-sub-1")
        self.assertEqual(user.email, "ada@example.org")
        self.assertEqual(user.first_name, "Ada")
        self.assertTrue(SocialIdentity.objects.filter(user=user).exists())

    def test_inactive_user_is_rejected(self):
        user = User.objects.create_user("ada", "ada@example.org", "pw", is_active=False)
        SocialIdentity.objects.create(provider="google", subject="g-sub-1", user=user)
        state = self._start()
        response = self._callback(state)
        self.assertEqual(response["Location"], reverse("sso:login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_provider_error_param_redirects_to_login(self):
        state = self._start()
        response = self.client.get(reverse("sso:social_callback", args=["google"]),
                                   {"error": "access_denied", "state": state})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("sso:login"))


@override_settings(**FACEBOOK_CREDS)
class FacebookMissingEmailTests(TestCase):
    FB_PAYLOAD = {"id": "fb-9", "first_name": "Bo", "last_name": "K"}  # email denied

    def _run_callback(self):
        response = self.client.get(reverse("sso:social_login", args=["facebook"]))
        state = parse_qs(urlparse(response["Location"]).query)["state"][0]
        with mock.patch("toto.social_login.views.http_requests.post",
                        return_value=_token_response()), \
             mock.patch("toto.social_login.views.http_requests.get",
                        return_value=_userinfo_response(dict(self.FB_PAYLOAD))):
            return self.client.get(reverse("sso:social_callback", args=["facebook"]),
                                   {"code": "auth-code", "state": state})

    def test_no_email_signup_off_rejected(self):
        response = self._run_callback()
        self.assertEqual(response["Location"], reverse("sso:login"))
        self.assertEqual(User.objects.count(), 0)

    @override_settings(TOTO_SOCIAL_SIGNUP=True)
    def test_no_email_signup_on_provisions_blank_email(self):
        self._run_callback()
        user = User.objects.get(username="facebook_fb-9")
        self.assertEqual(user.email, "")
        self.assertTrue(SocialIdentity.objects.filter(user=user, email="").exists())
