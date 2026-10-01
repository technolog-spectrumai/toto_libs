"""Every password door holds the sign-in lockout (2026-09-30): the sign-in form
(`password_login_view`, behind both core:login and sso:login), the desktop's
/api/login/ and Django's admin login. Each one pauses after the threshold,
refuses the right password while paused, says so in one sentence whatever
the name — and before the threshold answers exactly as it always did.

The rules themselves are `tests_signin_lockout`; zenobia's own doors, through
its whole middleware stack, are `zenobia.tests.test_signin_lockout`."""

import json
import re

from django.contrib import admin
from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from toto.api.auth_views import LoginApiView
from toto.core import signin_lockout as lockout
from toto.core.admin_login import SigninLockoutAdminAuthenticationForm
from toto.core.auth_views import password_login_view
from toto.core.models import Platform

User = get_user_model()

RIGHT = "Correct-horse-9"
HERE = "203.0.113.7"
ELSEWHERE = "198.51.100.20"
PAUSED = "Too many failed sign-in attempts. Signing in is paused for 15 minutes."


def _session(request):
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    request.user = AnonymousUser()
    request._messages = FallbackStorage(request)
    return request


@override_settings(
    AUTHENTICATION_BACKENDS=[lockout.BACKEND_PATH, "django.contrib.auth.backends.ModelBackend"],
    LOGIN_DELAY_AFTER=5, LOGIN_LOCK_AFTER=10, LOGIN_LOCK_MINUTES=15,
    LOGIN_ADDRESS_LOCK_AFTER=50, LOGIN_DELAY_MAX_SECONDS=60, LOGIN_FAILURE_WINDOW_MINUTES=15,
    # The 3-second session cooldown is its own suite (tests_more_auth); off
    # here, so what refuses is the lockout and nothing else.
    LOGIN_RETRY_COOLDOWN_SECONDS=0,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class _Door(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="Test", author="Tests", publication_year=2026,
                                active=True)
        cls.ada = User.objects.create_user("ada", password=RIGHT, is_staff=True)

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def pause(self, username="ada", address=HERE):
        """Ten wrong passwords through this door, each after its wait: the
        lockout's own clock is left alone, so the waits are skipped by
        clearing them between tries — what is counted is not."""
        for _ in range(10):
            self.knock(username, "wrong", address)
            keys = lockout._keys(username, lockout.address_bucket(address))
            lockout.ratelimit.forget(keys.pair_wait)


class FormDoorTests(_Door):
    def knock(self, username, password, address=HERE):
        request = _session(RequestFactory().post(
            "/sso/login/", {"username": username, "password": password}, REMOTE_ADDR=address))
        response = password_login_view(request, template_name="oya/login.html",
                                       page_title="Sign in")
        return request, response

    def test_before_the_threshold_a_wrong_password_reads_as_it_always_did(self):
        _, response = self.knock("ada", "wrong")
        self.assertContains(response, "Invalid username or password.")

    def test_a_paused_pair_refuses_the_right_password_and_says_for_how_long(self):
        self.pause()
        request, response = self.knock("ada", RIGHT)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(SESSION_KEY, request.session)
        self.assertContains(response, PAUSED)
        self.assertNotContains(response, "Invalid username or password.")
        # The page counts the pause down on the same bar the cooldown uses.
        self.assertContains(response, 'data-login-cooldown="900"')

    def test_the_wait_before_the_pause_is_said_too(self):
        for _ in range(5):
            self.knock("ada", "wrong")
        _, response = self.knock("ada", RIGHT)
        self.assertContains(response, "Wait 1 second and try again.")

    def test_an_unknown_name_gets_the_same_page_as_a_known_one(self):
        self.pause("ghost")
        self.pause("ada")
        _, ghost = self.knock("ghost", RIGHT)
        _, known = self.knock("ada", RIGHT)
        self.assertContains(ghost, PAUSED)

        def page(response, name):
            # The CSRF tokens are random per request; everything else must match.
            text = re.sub(r"[A-Za-z0-9]{32,}", "TOKEN", response.content.decode())
            return text.replace(f'value="{name}"', 'value="NAME"')

        self.assertEqual(page(ghost, "ghost"), page(known, "ada"))

    def test_another_address_still_signs_in(self):
        self.pause()
        request, response = self.knock("ada", RIGHT, address=ELSEWHERE)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(request.session[SESSION_KEY], str(self.ada.pk))

    def test_the_log_names_the_account_by_its_id_and_never_the_name_typed(self):
        """The log wrote the name typed at every failed sign-in — often an
        e-mail address — and the username at every sign-in and sign-out
        (2026-10-01, 37c.21). The chain's AUTH records keep what may be read."""
        from toto.core.auth_views import password_logout_view

        with self.assertLogs("toto.core.auth_views", level="INFO") as logs:
            self.knock("ada@example.com", "wrong")
            request, _ = self.knock("ada", RIGHT)
            request.user = self.ada
            password_logout_view(request)
        text = "\n".join(logs.output)
        self.assertNotIn("ada@example.com", text)
        self.assertNotIn("'ada'", text)
        self.assertIn("Failed sign-in attempt.", text)
        self.assertIn(f"Account {self.ada.pk} signed in.", text)
        self.assertIn(f"Account {self.ada.pk} signed out.", text)


class ApiDoorTests(_Door):
    def knock(self, username, password, address=HERE, body=None):
        request = _session(RequestFactory().post(
            "/api/login/", body if body is not None else json.dumps(
                {"username": username, "password": password}),
            content_type="application/json", REMOTE_ADDR=address))
        return LoginApiView.as_view()(request)

    def test_before_the_threshold_a_wrong_password_is_still_401(self):
        response = self.knock("ada", "wrong")
        self.assertEqual((response.status_code, json.loads(response.content)),
                         (401, {"error": "Invalid credentials."}))

    def test_a_paused_pair_is_429_with_the_time_left_and_no_token(self):
        self.pause()
        response = self.knock("ada", RIGHT)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "900")
        self.assertEqual(json.loads(response.content), {"error": PAUSED, "retry_after": 900})

    def test_an_unknown_name_gets_the_same_answer_byte_for_byte(self):
        self.pause("ghost")
        self.pause("ada")
        ghost, known = self.knock("ghost", RIGHT), self.knock("ada", RIGHT)
        self.assertEqual((ghost.status_code, ghost.content, ghost["Retry-After"]),
                         (known.status_code, known.content, known["Retry-After"]))

    def test_another_address_still_gets_its_token(self):
        self.pause()
        response = self.knock("ada", RIGHT, address=ELSEWHERE)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(response.content)["token"])

    def test_a_body_that_is_not_an_object_is_400_not_500(self):
        for body in ("[]", '"ada"', "3", '{"username": 7, "password": "x"}',
                     '{"username": "ada", "password": ["x"]}'):
            with self.subTest(body=body):
                self.assertEqual(self.knock("", "", body=body).status_code, 400)


class AdminDoorTests(_Door):
    def knock(self, username, password, address=HERE):
        return self.client.post(reverse("admin:login"),
                                {"username": username, "password": password,
                                 "next": reverse("admin:index")}, REMOTE_ADDR=address)

    def test_the_default_admin_site_uses_the_form_that_says_why(self):
        self.assertIs(admin.site.login_form, SigninLockoutAdminAuthenticationForm)

    def test_before_the_threshold_a_wrong_password_reads_as_it_always_did(self):
        response = self.knock("ada", "wrong")
        self.assertContains(response, "Please enter the correct username and password")

    def test_a_paused_pair_refuses_the_right_password_and_says_why(self):
        self.pause()
        response = self.knock("ada", RIGHT)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, PAUSED)
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_an_unknown_name_gets_the_same_sentence(self):
        self.pause("ghost")
        self.assertContains(self.knock("ghost", RIGHT), PAUSED)

    def test_another_address_still_signs_in(self):
        self.pause()
        response = self.knock("ada", RIGHT, address=ELSEWHERE)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session[SESSION_KEY], str(self.ada.pk))
