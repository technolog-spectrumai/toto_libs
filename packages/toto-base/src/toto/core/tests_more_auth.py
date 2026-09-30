"""The one password door (`core.auth_views`) and its cooldowns (2026-09-29).

`core:login` and `sso:login` both delegate to `password_login_view`, so these
drive it directly: what a right and a wrong password do, what the retry
cooldown refuses (the right password too, while it runs), and the other two
cooldowns that share its machinery."""

import time
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from toto.audit.models import AuditRecord
from toto.core import auth_cooldown as cooldown
from toto.core.auth_views import password_login_view, password_logout_view
from toto.core.models import Platform

User = get_user_model()
KEY = cooldown.LOGIN_RETRY_COOLDOWN_SESSION_KEY


def _request(method="get", data=None, *, path="/core/login/", user=None, session=None):
    request = getattr(RequestFactory(), method)(path, data or {})
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.update(session or {})
    request.session.save()
    request.user = user or AnonymousUser()
    request._messages = FallbackStorage(request)
    return request


def _door(request):
    return password_login_view(request, template_name="oya/login.html", page_title="Login")


@override_settings(LOGIN_RETRY_COOLDOWN_SECONDS=3)
class LoginDoorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="Test", author="Tests", publication_year=2026,
                                active=True)
        cls.ada = User.objects.create_user("ada", password="Correct-horse-9")

    def test_a_signed_in_visitor_is_sent_on_to_where_they_were_going(self):
        response = _door(_request(data={"next": "/vault/"}, user=self.ada))
        self.assertEqual((response.status_code, response["Location"]), (302, "/vault/"))

    def test_a_signed_in_visitor_with_nowhere_to_go_lands_on_the_dashboard(self):
        response = _door(_request(user=self.ada))
        self.assertEqual(response["Location"], reverse("core:dashboard"))

    def test_the_right_password_signs_in_and_follows_next(self):
        request = _request("post", {"username": "ada", "password": "Correct-horse-9",
                                    "next": "/wiki/"})
        response = _door(request)
        self.assertEqual((response.status_code, response["Location"]), (302, "/wiki/"))
        self.assertEqual(request.session[SESSION_KEY], str(self.ada.pk))

    def test_a_wrong_password_is_refused_and_starts_the_cooldown(self):
        request = _request("post", {"username": "ada", "password": "wrong"})
        with self.assertLogs("toto.core.auth_views", "WARNING"):
            response = _door(request)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid username or password.")
        self.assertNotIn(SESSION_KEY, request.session)
        self.assertAlmostEqual(request.session[KEY], time.time() + 3, delta=2)

    def test_during_the_cooldown_even_the_right_password_waits(self):
        request = _request("post", {"username": "ada", "password": "Correct-horse-9"},
                           session={KEY: time.time() + 1.5})
        response = _door(request)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please wait 2 seconds before trying again.")
        self.assertNotIn(SESSION_KEY, request.session)
        # Refused before the credentials were looked at: no sign-in, no failure.
        self.assertFalse(AuditRecord.objects.filter(action__startswith="AUTH.LOG").exists())

    def test_a_spent_cooldown_lets_the_right_password_in_and_is_cleared(self):
        request = _request("post", {"username": "ada", "password": "Correct-horse-9"},
                           session={KEY: time.time() - 1})
        self.assertEqual(_door(request).status_code, 302)
        self.assertNotIn(KEY, request.session)

    def test_a_get_is_never_cooled_down(self):
        request = _request(session={KEY: time.time() + 30})
        response = _door(request)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Please wait")

    def test_an_incomplete_form_is_refused_and_cools_down_too(self):
        request = _request("post", {"username": "ada"})
        response = _door(request)
        self.assertContains(response, "Enter your username and password.")
        self.assertIn(KEY, request.session)

    @override_settings(LOGIN_RETRY_COOLDOWN_SECONDS=0)
    def test_a_zero_cooldown_never_makes_anybody_wait(self):
        request = _request("post", {"username": "ada", "password": "wrong"})
        with self.assertLogs("toto.core.auth_views", "WARNING"):
            _door(request)
        self.assertNotIn(KEY, request.session)

    def test_next_never_leaves_this_site(self):
        # Skipped as a suspected bug until 2026-09-30: `next` was followed
        # unchecked, an open redirect on both doors (toto.core.safe_next).
        request = _request("post", {"username": "ada", "password": "Correct-horse-9",
                                    "next": "https://evil.example.com/phish"})
        self.assertFalse(_door(request)["Location"].startswith("https://evil."))
        out = password_logout_view(_request(data={"next": "https://evil.example.com/"},
                                            user=self.ada))
        self.assertFalse(out["Location"].startswith("https://evil."))


class LogoutDoorTests(TestCase):
    def test_signing_out_ends_the_session_and_lands_on_the_dashboard(self):
        ada = User.objects.create_user("ada", password="pw")
        request = _request(session={SESSION_KEY: str(ada.pk)}, user=ada)
        response = password_logout_view(request)
        self.assertEqual(response["Location"], reverse("core:dashboard"))
        self.assertNotIn(SESSION_KEY, request.session)
        self.assertFalse(request.user.is_authenticated)

    def test_signing_out_follows_a_local_next(self):
        response = password_logout_view(_request(data={"next": "/welcome/"}))
        self.assertEqual(response["Location"], "/welcome/")


class CooldownArithmeticTests(SimpleTestCase):
    def request(self, **session):
        # The cooldowns only read and write the session mapping.
        return SimpleNamespace(session=dict(session))

    @override_settings(LOGIN_RETRY_COOLDOWN_SECONDS=-5)
    def test_a_negative_setting_is_no_cooldown(self):
        self.assertEqual(cooldown.login_retry_cooldown_seconds(), 0)

    def test_what_remains_is_rounded_up_to_a_whole_second(self):
        with mock.patch.object(cooldown.time, "time", return_value=1000.0):
            request = self.request(**{KEY: 1000.2})
            self.assertEqual(cooldown.login_retry_cooldown_remaining(request), 1)

    def test_a_past_deadline_leaves_nothing_to_wait(self):
        with mock.patch.object(cooldown.time, "time", return_value=1000.0):
            self.assertEqual(cooldown.login_retry_cooldown_remaining(self.request(**{KEY: 10})), 0)
            self.assertEqual(cooldown.login_retry_cooldown_remaining(self.request()), 0)

    @override_settings(CAPTCHA_RETRY_COOLDOWN_SECONDS=7)
    def test_the_captcha_cooldown_is_its_own_clock(self):
        request = self.request()
        with mock.patch.object(cooldown.time, "time", return_value=1000.0):
            cooldown.start_captcha_retry_cooldown(request)
            self.assertEqual(cooldown.captcha_retry_cooldown_remaining(request), 7)
            self.assertEqual(cooldown.login_retry_cooldown_remaining(request), 0)
            cooldown.clear_captcha_retry_cooldown(request)
            self.assertEqual(cooldown.captcha_retry_cooldown_remaining(request), 0)

    def test_a_reset_request_waits_a_minute_by_default(self):
        request = self.request()
        with override_settings(), mock.patch.object(cooldown.time, "time", return_value=50.0):
            from django.conf import settings

            if hasattr(settings, "RESET_REQUEST_COOLDOWN_SECONDS"):
                self.skipTest("this host sets its own reset cooldown")
            cooldown.start_reset_request_cooldown(request)
            self.assertEqual(cooldown.reset_request_cooldown_remaining(request), 60)

    @override_settings(RESET_REQUEST_COOLDOWN_SECONDS=0)
    def test_starting_a_zero_cooldown_clears_a_running_one(self):
        request = self.request(**{cooldown.RESET_REQUEST_COOLDOWN_SESSION_KEY: time.time() + 99})
        cooldown.start_reset_request_cooldown(request)
        self.assertNotIn(cooldown.RESET_REQUEST_COOLDOWN_SESSION_KEY, request.session)
