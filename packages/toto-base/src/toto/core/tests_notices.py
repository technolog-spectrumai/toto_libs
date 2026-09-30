"""``toto.core.notices.send_notice`` (2026-09-30): the one seam security
notices leave through — sent when it can be, logged and ``False`` when it
cannot, never an exception into the request that caused it.
"""

from __future__ import annotations

from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings

from toto.core.models import Platform
from toto.core.notices import KINDS, send_notice
from toto.people.models import Person

User = get_user_model()
LOCMEM = "django.core.mail.backends.locmem.EmailBackend"


@override_settings(EMAIL_BACKEND=LOCMEM)
class SendNoticeTests(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Zenobia Test", author="Tests",
                                publication_year=2026, active=True)
        self.user = User.objects.create_user("ada", "ada@example.test", "pw")

    def test_a_notice_is_sent_to_the_account_address(self):
        self.assertTrue(send_notice(self.user, "password_changed",
                                    {"address": "203.0.113.7"}))
        [notice] = mail.outbox
        self.assertEqual(notice.to, ["ada@example.test"])
        self.assertEqual(notice.subject, "Zenobia Test: your password was changed")
        self.assertIn("Hello ada,", notice.body)
        self.assertIn("203.0.113.7", notice.body)

    def test_the_time_is_in_the_members_zone(self):
        Person.objects.create(user=self.user, display_name="Ada", timezone="Asia/Tokyo")
        self.user.refresh_from_db()
        send_notice(self.user, "password_changed")
        self.assertIn("JST", mail.outbox[0].body)

    def test_an_unknown_kind_is_refused_not_raised(self):
        with self.assertLogs("toto.core.notices", "ERROR"):
            self.assertFalse(send_notice(self.user, "no_such_notice"))
        self.assertEqual(mail.outbox, [])

    def test_no_address_sends_nothing(self):
        self.user.email = ""
        self.assertFalse(send_notice(self.user, "password_changed"))
        self.assertEqual(mail.outbox, [])

    def test_a_failing_backend_is_logged_not_raised(self):
        with mock.patch("django.core.mail.EmailMessage.send",
                        side_effect=ConnectionRefusedError("ada@example.test")):
            with self.assertLogs("toto.core.notices", "WARNING") as logs:
                self.assertFalse(send_notice(self.user, "password_changed"))
        # The class, not the text: an SMTP error's text can carry the address.
        self.assertIn("ConnectionRefusedError", logs.output[0])
        self.assertNotIn("ada@example.test", logs.output[0])

    def test_a_template_that_fails_is_logged_not_raised(self):
        with mock.patch("toto.core.notices.loader.render_to_string",
                        side_effect=RuntimeError("boom")):
            with self.assertLogs("toto.core.notices", "WARNING"):
                self.assertFalse(send_notice(self.user, "password_changed"))

    def test_every_kind_renders(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                self.assertTrue(send_notice(self.user, kind))
