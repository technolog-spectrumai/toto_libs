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


@override_settings(**GOOGLE_CREDS)
class FederatedAccountIsNotTakeableTests(TestCase):
    """The verified-email match must never adopt a FEDERATED account.

    `sso_client` closed the mirror image of this and its `FederatedIdentity`
    docstring names the attack: an account whose `email`, names and — under the
    `roles` scope — `is_staff`/`is_superuser`/`is_active` are rewritten from an
    upstream provider's claims on every sign-in. Reaching it by proving control
    of a Google address is reaching it without the provider's agreement.

    Step 2 of `_resolve_user` runs BEFORE any signup flag, so this holds with
    TOTO_SOCIAL_SIGNUP off and on alike — both are asserted below.
    """

    def _start(self, provider="google"):
        response = self.client.get(reverse("sso:social_login", args=[provider]))
        return parse_qs(urlparse(response["Location"]).query)["state"][0]

    def _callback(self, state, provider="google", claims=None):
        with mock.patch("toto.social_login.views.http_requests.post",
                        return_value=_token_response()), \
             mock.patch("toto.social_login.views.http_requests.get",
                        return_value=_userinfo_response(claims or dict(GOOGLE_CLAIMS))):
            return self.client.get(reverse("sso:social_callback", args=[provider]),
                                   {"code": "auth-code", "state": state})

    def _federate(self, user):
        """Mark `user` as belonging to an upstream provider.

        Patched rather than built from real rows: `FederatedIdentity` needs an
        `OIDCProviderConfig`, which only the pairing flow may write (there is
        no management command and no environment path, by design). What is
        under test is the refusal, not the consumer app's schema.
        """
        return mock.patch("toto.social_login.views._is_federated",
                          side_effect=lambda u: u.pk == user.pk)

    def test_a_federated_account_is_not_adopted_by_email(self):
        staff = User.objects.create_user("oidc_zsub", "ada@example.org", "pw",
                                         is_staff=True)
        state = self._start()
        with self._federate(staff):
            response = self._callback(state)
        # Refused the way every other unmatched sign-in is: back to the login
        # page with a message, no session, no identity written.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("sso:login"))
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertFalse(SocialIdentity.objects.filter(user=staff).exists())

    @override_settings(TOTO_SOCIAL_SIGNUP=True)
    def test_signup_being_on_does_not_open_the_door_either(self):
        """Nor does it quietly mint a SECOND account on that email — the
        refusal is a refusal, not a fallthrough to step 3."""
        staff = User.objects.create_user("oidc_zsub", "ada@example.org", "pw",
                                         is_staff=True)
        state = self._start()
        with self._federate(staff):
            response = self._callback(state)
        self.assertEqual(response["Location"], reverse("sso:login"))
        self.assertEqual(User.objects.count(), 1)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_a_local_account_on_the_same_email_still_matches(self):
        """The half that must NOT break: matching a verified email onto a
        LOCAL account is the useful behaviour this flow exists for."""
        local = User.objects.create_user("ada", "ada@example.org", "pw")
        state = self._start()
        with self._federate(User(pk=-1)):   # nobody is federated
            response = self._callback(state)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), local.pk)
        self.assertTrue(SocialIdentity.objects.filter(user=local).exists())

    def test_a_federated_user_who_linked_deliberately_still_signs_in(self):
        """Refusing the MATCH does not refuse the identity. A federated user
        who deliberately linked Google keeps that route — step 1 finds the
        recorded (provider, subject) before step 2 is ever reached."""
        staff = User.objects.create_user("oidc_zsub", "ada@example.org", "pw",
                                         is_staff=True)
        SocialIdentity.objects.create(provider="google", subject="g-sub-1",
                                      user=staff, email="ada@example.org")
        state = self._start()
        with self._federate(staff):
            response = self._callback(state)
        self.assertEqual(int(self.client.session["_auth_user_id"]), staff.pk)


class FederationProbeTests(TestCase):
    """`_is_federated` itself: the two answers, and the failure mode."""

    def test_a_host_without_the_consumer_app_has_no_federated_accounts(self):
        from toto.social_login.views import _is_federated

        user = User.objects.create_user("ada", "ada@example.org", "pw")
        # toto.sso_client is not installed in this harness.
        self.assertFalse(_is_federated(user))

    def test_an_unanswerable_question_refuses(self):
        """Fails CLOSED. One person signing in with a password instead of
        Google is a smaller cost than somebody else's staff account."""
        from toto.social_login import views

        user = User.objects.create_user("ada", "ada@example.org", "pw")
        with mock.patch("django.apps.apps.is_installed", return_value=True), \
             mock.patch.dict("sys.modules", {"toto.sso_client.models": None}):
            self.assertTrue(views._is_federated(user))
