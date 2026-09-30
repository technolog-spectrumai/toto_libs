"""My account, the password section (2026-09-30): Django's PasswordChangeForm
with the host's validators, this session kept, every other session and every
desktop token ended, ``AUTH.PASSWORD_CHANGED`` on the chain and a notice
mailed through ``toto.core.notices``.
"""

from __future__ import annotations

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from toto.api.tokens import user_for_session_key
from toto.audit.models import AuditRecord
from toto.core.models import Platform
from toto.core.user_sessions import end_other_sessions, session_keys_for
from toto.people.models import Person

User = get_user_model()
LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
OLD = "Correct-horse-9"
NEW = "Battery-staple-41"


@override_settings(EMAIL_BACKEND=LOCMEM)
class PasswordTestCase(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Test", author="Tests",
                                publication_year=2026, active=True)
        self.user = User.objects.create_user("ada", "ada@example.test", OLD)
        Person.objects.create(user=self.user, display_name="Ada")
        self.client.force_login(self.user)

    def change(self, old=OLD, new=NEW, again=None, client=None):
        return (client or self.client).post(reverse("account:password"), {
            "old_password": old, "new_password1": new,
            "new_password2": new if again is None else again,
        })

    def token(self):
        """The key a desktop is handed at /api/login/."""
        response = Client().post("/api/login/",
                                 json.dumps({"username": "ada", "password": OLD}),
                                 content_type="application/json")
        self.assertEqual(response.status_code, 200)
        return response.json()["token"]

    def other_browser(self):
        other = Client()
        other.force_login(self.user)
        return other

    def records(self):
        return AuditRecord.objects.filter(action="AUTH.PASSWORD_CHANGED")

    def still_old(self):
        self.user.refresh_from_db()
        return self.user.check_password(OLD)


class PageTests(PasswordTestCase):
    def test_the_section_posts_to_its_own_door(self):
        self.assertEqual(reverse("account:password"), "/account/password/")
        response = self.client.get(reverse("account:home"))
        self.assertContains(response, 'action="/account/password/"')
        self.assertContains(response, 'name="old_password"')

    def test_the_get_is_refused(self):
        self.assertEqual(self.client.get(reverse("account:password")).status_code, 405)

    def test_signed_out_changes_nothing(self):
        self.client.logout()
        response = self.change()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.still_old())

    def test_a_federated_account_has_no_password_here(self):
        self.user.set_unusable_password()
        self.user.save()
        self.client.force_login(self.user)
        page = self.client.get(reverse("account:home"))
        self.assertNotContains(page, 'name="old_password"')
        self.assertContains(page, "signs in through another service")
        response = self.change(old="")
        self.assertRedirects(response, reverse("account:home"), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertFalse(self.user.has_usable_password())
        self.assertFalse(self.records().exists())


class RefusalTests(PasswordTestCase):
    def test_a_wrong_current_password_is_refused(self):
        response = self.change(old="not-my-password")
        self.assertEqual(response.status_code, 400)
        self.assertIn("old_password", response.context["password_form"].errors)
        self.assertTrue(self.still_old())
        self.assertFalse(self.records().exists())
        self.assertEqual(mail.outbox, [])

    def test_a_weak_new_password_is_refused(self):
        for weak in ("short", "password123", "12345678901", "ada-ada"):
            with self.subTest(weak=weak):
                response = self.change(new=weak)
                self.assertEqual(response.status_code, 400)
                self.assertIn("new_password2", response.context["password_form"].errors)
                self.assertTrue(self.still_old())
        self.assertFalse(self.records().exists())

    def test_two_different_new_passwords_are_refused(self):
        response = self.change(again=NEW + "x")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(self.still_old())

    def test_a_refusal_ends_no_session(self):
        other = self.other_browser()
        token = self.token()
        self.change(old="not-my-password")
        self.assertEqual(other.get(reverse("account:home")).status_code, 200)
        self.assertEqual(user_for_session_key(token, door="api"), self.user)


class SuccessTests(PasswordTestCase):
    def test_the_password_changes_and_this_session_stays(self):
        response = self.change()
        self.assertRedirects(response, reverse("account:home"), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW))
        # Still signed in here, re-signed with the new hash.
        self.assertEqual(self.client.get(reverse("account:home")).status_code, 200)

    def test_another_session_and_an_old_token_are_ended(self):
        other = self.other_browser()
        other_key = other.session.session_key
        token = self.token()
        self.change()
        self.assertFalse(SessionStore().exists(other_key))
        self.assertFalse(SessionStore().exists(token))
        self.assertIsNone(user_for_session_key(token, door="api"))
        self.assertNotEqual(other.get(reverse("account:home")).status_code, 200)
        # What is left is this session alone.
        self.assertEqual(session_keys_for(self.user), [self.client.session.session_key])

    def test_the_hash_check_kills_a_token_the_sweep_missed(self):
        # Stage 31: a token is checked against the password's session hash,
        # so even a session the sweep could not end (another engine, a row
        # that failed to delete) stops signing anybody in.
        token = self.token()
        with mock.patch("toto.socialhub.views.account.end_other_sessions", return_value=0):
            self.change()
        self.assertTrue(SessionStore().exists(token))
        self.assertIsNone(user_for_session_key(token, door="api"))
        refused = AuditRecord.objects.get(action="AUTH.TOKEN_REFUSED")
        self.assertEqual(refused.metadata["reason"], "session_hash")

    def test_the_change_is_on_the_chain_without_a_password(self):
        self.other_browser()
        self.token()
        self.change()
        record = self.records().get()
        self.assertEqual(record.actor_user, self.user)
        self.assertEqual(record.object_id, str(self.user.pk))
        self.assertEqual(record.metadata["sessions_ended"], 2)
        self.assertEqual(record.request_source["path"], "/account/password/")
        blob = json.dumps([record.metadata, record.changes, record.request_source])
        self.assertNotIn(OLD, blob)
        self.assertNotIn(NEW, blob)

    def test_a_notice_is_mailed_to_the_member(self):
        self.other_browser()
        self.change()
        self.assertEqual(len(mail.outbox), 1)
        notice = mail.outbox[0]
        self.assertEqual(notice.to, ["ada@example.test"])
        self.assertIn("password was changed", notice.subject)
        self.assertEqual(notice.extra_headers["X-Toto-Notice"], "password_changed")
        self.assertIn("1 other sign-in was ended", notice.body)
        self.assertNotIn(NEW, notice.body)
        self.assertNotIn(OLD, notice.body)

    def test_no_address_no_notice_and_the_change_still_stands(self):
        User.objects.filter(pk=self.user.pk).update(email="")
        response = self.change()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mail.outbox, [])
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW))

    def test_a_mail_server_that_fails_does_not_fail_the_change(self):
        with mock.patch("django.core.mail.EmailMessage.send", side_effect=OSError("smtp down")):
            response = self.change()
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW))
        self.assertTrue(self.records().exists())


class SessionSweepTests(PasswordTestCase):
    def test_only_this_members_sessions_and_not_the_kept_one(self):
        bob = User.objects.create_user("bob", password="pw")
        bobs = Client()
        bobs.force_login(bob)
        other = self.other_browser()
        mine = self.client.session.session_key
        self.assertEqual(end_other_sessions(self.user, keep=mine), 1)
        self.assertTrue(SessionStore().exists(mine))
        self.assertFalse(SessionStore().exists(other.session.session_key))
        self.assertTrue(SessionStore().exists(bobs.session.session_key))

    @override_settings(SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies")
    def test_an_engine_that_cannot_be_listed_ends_nothing(self):
        self.assertEqual(end_other_sessions(self.user, keep=""), 0)


LOCKOUT = dict(
    AUTHENTICATION_BACKENDS=["toto.core.signin_lockout.SigninLockoutBackend",
                             "django.contrib.auth.backends.ModelBackend"],
    LOGIN_DELAY_AFTER=0, LOGIN_LOCK_AFTER=3, LOGIN_LOCK_MINUTES=15,
    LOGIN_ADDRESS_LOCK_AFTER=0, LOGIN_FAILURE_WINDOW_MINUTES=15)


@override_settings(**LOCKOUT)
class GuessTests(PasswordTestCase):
    """Review 2026-10-01: wrong current passwords on this form were not
    counted by the sign-in lockout, so a borrowed session could guess the
    password without end."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.addCleanup(cache.clear)
        super().setUp()

    def test_wrong_current_passwords_pause_the_form_and_the_sign_in(self):
        from django.contrib.auth import authenticate
        from django.test import RequestFactory

        for _ in range(3):
            self.assertEqual(self.change(old="not-my-password").status_code, 400)
        # Paused now: even the right password changes nothing here...
        response = self.change()
        self.assertRedirects(response, reverse("account:home") + "#password",
                             fetch_redirect_response=False)
        self.assertTrue(self.still_old())
        # ...and the same name from the same address is paused at sign-in too.
        request = RequestFactory().post("/sso/login/", REMOTE_ADDR="127.0.0.1")
        self.assertIsNone(authenticate(request, username="ada", password=OLD))

    def test_a_weak_new_password_is_not_a_guess(self):
        for _ in range(4):
            self.change(new="short")
        self.change()
        self.assertFalse(self.still_old())

    def test_the_passwords_are_hidden_from_error_reports(self):
        from django.test import RequestFactory

        from toto.socialhub.views.account import account_password

        request = RequestFactory().post("/account/password/", {"old_password": "x"})
        request.user = self.user
        with mock.patch("toto.socialhub.views.account.PasswordChangeForm",
                        side_effect=RuntimeError("boom")), \
                self.assertRaises(RuntimeError):
            account_password(request)
        self.assertEqual(set(request.sensitive_post_parameters),
                         {"old_password", "new_password1", "new_password2"})
