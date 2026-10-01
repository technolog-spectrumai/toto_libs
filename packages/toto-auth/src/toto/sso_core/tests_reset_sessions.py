"""A password reset ends every sign-in of the account at once (2026-10-01,
37c.4).

Before, a reset ended the account's other sessions only through the session
hash check at their next request, and their rows stayed on My account's
Sessions list until it was read. Now both flows — the mailed link and a
patron's one-time link — end every session of the account the moment the
new password is set, browsers and desktop tokens alike, with their
``UserSession`` rows (``toto.core.user_sessions.end_other_sessions``, none
kept); the chain and the "password changed" notice say how many.
"""

import json

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.contrib.sessions.backends.db import SessionStore
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from toto.core.models import Platform, UserSession

from . import recovery

User = get_user_model()
LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
OLD = "Correct-horse-9"
NEW = "a-much-better-pass-42"


@override_settings(EMAIL_BACKEND=LOCMEM)
class ResetSessionsCase(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Toto", author="T", publication_year=2026,
                                active=True)
        self.user = User.objects.create_user("ada", "ada@x.test", OLD)

    def browser(self, user=None):
        """Another browser, signed in as ``user`` (Ada by default)."""
        other = Client()
        other.force_login(user or self.user)
        return other

    def token(self):
        """The key a desktop is handed at /api/login/."""
        response = Client().post("/api/login/",
                                 json.dumps({"username": "ada", "password": OLD}),
                                 content_type="application/json")
        self.assertEqual(response.status_code, 200)
        return response.json()["token"]

    def confirm_url(self):
        # The token covers the last sign-in, which the sessions above moved.
        self.user.refresh_from_db()
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        return reverse("sso:password_reset_confirm",
                       args=[uid, default_token_generator.make_token(self.user)])

    def reset_by_email(self, client=None, again=NEW):
        return (client or self.client).post(self.confirm_url(), {
            "new_password1": NEW, "new_password2": again})

    def assertEnded(self, *keys):
        for key in keys:
            self.assertFalse(SessionStore().exists(key))
            self.assertFalse(UserSession.objects.filter(session_key=key).exists())

    def assertAlive(self, *keys):
        for key in keys:
            self.assertTrue(SessionStore().exists(key))
            self.assertTrue(UserSession.objects.filter(session_key=key).exists())

    def reset_record(self):
        from django.apps import apps

        if not apps.is_installed("toto.audit"):
            self.skipTest("audit not installed on this host")
        from toto.audit.models import AuditRecord

        return AuditRecord.objects.get(action="AUTH.PASSWORD_RESET")


class EmailLinkTests(ResetSessionsCase):
    def test_every_session_and_its_row_end_with_the_reset(self):
        browser = self.browser()
        key, token = browser.session.session_key, self.token()
        self.assertAlive(key, token)
        response = self.reset_by_email()
        self.assertRedirects(response, reverse("sso:password_reset_complete"),
                             fetch_redirect_response=False)
        self.assertEnded(key, token)
        self.assertFalse(UserSession.objects.filter(user=self.user).exists())

    def test_another_members_sessions_stay(self):
        bob = User.objects.create_user("bob", "bob@x.test", "pw")
        bobs = self.browser(bob).session.session_key
        self.reset_by_email()
        self.assertAlive(bobs)

    def test_a_refused_reset_ends_nothing(self):
        key = self.browser().session.session_key
        response = self.reset_by_email(again="something-else-42")
        self.assertEqual(response.status_code, 200)
        self.assertAlive(key)

    def test_a_browser_of_the_member_resetting_is_ended_too(self):
        # Signed in with the old password like the others: nothing is kept,
        # and the reset still answers as it does for anybody else.
        self.client.force_login(self.user)
        key = self.client.session.session_key
        response = self.reset_by_email()
        self.assertRedirects(response, reverse("sso:password_reset_complete"),
                             fetch_redirect_response=False)
        self.assertEnded(key)

    def test_the_chain_and_the_notice_count_them(self):
        self.browser()
        self.token()
        self.reset_by_email()
        record = self.reset_record()
        self.assertEqual(record.metadata, {"flow": "email", "sessions_ended": 2})
        notices = [m for m in mail.outbox
                   if m.extra_headers.get("X-Toto-Notice") == "password_changed"]
        self.assertEqual(len(notices), 1)
        self.assertIn("2 other sign-ins were ended", notices[0].body)


class RecoveryLinkTests(ResetSessionsCase):
    def setUp(self):
        super().setUp()
        from toto.people.models import Person

        self.patron = User.objects.create_user("patron", "patron@x.test", "pw")
        Person.objects.create(user=self.patron, display_name="Patron")
        Person.objects.create(user=self.user, display_name="Ada",
                              patron=self.patron.community_profile)

    def recovery_url(self):
        ticket = recovery.file_request("ada")
        self.assertIsNotNone(ticket)
        return reverse("sso:password_reset_recover",
                       args=[recovery.approve(ticket, self.patron)])

    def test_every_session_and_its_row_end_with_the_reset(self):
        key, token = self.browser().session.session_key, self.token()
        url = self.recovery_url()
        response = self.client.post(url, {"new_password1": NEW, "new_password2": NEW})
        self.assertRedirects(response, reverse("sso:password_reset_complete"),
                             fetch_redirect_response=False)
        self.assertEnded(key, token)
        self.assertEqual(self.reset_record().metadata,
                         {"flow": "recovery", "sessions_ended": 2})

    def test_the_patrons_own_session_stays(self):
        patrons = self.browser(self.patron).session.session_key
        url = self.recovery_url()
        self.client.post(url, {"new_password1": NEW, "new_password2": NEW})
        self.assertAlive(patrons)
