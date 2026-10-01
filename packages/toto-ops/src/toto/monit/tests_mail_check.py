"""The Mail check (2026-10-01): WARN when the last ``MAIL_FAILURES_WARN``
notices could not be delivered after all their tries, read off the outcomes
``toto.core.notices`` keeps; one delivery makes it OK again. Sends are driven
through the real task, ``toto.core.tasks.deliver_notice``.
"""

from __future__ import annotations

import smtplib
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings

from toto.core.models import NoticeDelivery, Platform
from toto.core.tasks import deliver_notice
from toto.monit import alerts, record

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
SEND = "django.core.mail.EmailMessage.send"


def notice(kind="check_alert"):
    return {"kind": kind, "to": "ops@example.test", "subject": "Backups is failing",
            "body": "Newest 3 days ago."}


@override_settings(EMAIL_BACKEND=LOCMEM, NOTICES_VIA_WORKER=False)
class MailCheckTests(TestCase):
    def give_up(self, times, kind="check_alert"):
        refusal = smtplib.SMTPAuthenticationError(535, b"5.7.8 ops@example.test refused")
        with mock.patch(SEND, side_effect=refusal):
            for _ in range(times):
                deliver_notice.apply(args=[notice(kind)])

    def test_nothing_sent_yet_is_fine(self):
        check = record.check_mail()
        self.assertEqual((check.key, check.status), ("mail", record.OK))
        self.assertEqual(check.summary, "No notice has been sent yet.")
        self.assertEqual(check.detail, "")

    def test_it_turns_warn_when_the_last_sends_failed(self):
        self.give_up(2)
        check = record.check_mail()
        self.assertEqual(check.status, record.OK)
        self.assertEqual(check.summary, "2 notices in a row could not be delivered "
                                        "(a warning at 3).")
        self.give_up(1, kind="new_sign_in")
        check = record.check_mail()
        self.assertEqual(check.status, record.WARN)
        self.assertEqual(check.summary, "The last 3 notices could not be delivered. "
                                        "Last error: SMTPAuthenticationError (535).")
        self.assertIn("check_alert: Failed, try 5, SMTPAuthenticationError (535)", check.detail)
        self.assertIn("new_sign_in: Failed, try 5", check.detail)
        self.assertEqual(check.value, "3")
        self.assertNotIn("ops@example.test", check.summary + check.detail)

    def test_one_delivery_makes_it_fine_again(self):
        self.give_up(3)
        self.assertEqual(record.check_mail().status, record.WARN)
        with mock.patch(SEND, return_value=1):
            deliver_notice.apply(args=[notice("password_changed")])
        check = record.check_mail()
        self.assertEqual(check.status, record.OK)
        self.assertTrue(check.summary.startswith("The last notice was sent "), check.summary)
        # The kinds that failed still say so until they are sent again.
        self.assertIn("check_alert: Failed", check.detail)

    def test_a_notice_being_retried_is_shown_not_counted(self):
        NoticeDelivery.objects.create(purpose="check_alert", status=NoticeDelivery.RETRYING,
                                      tries=2, error="SMTPServerDisconnected")
        check = record.check_mail()
        self.assertEqual(check.status, record.OK)
        self.assertEqual(check.summary, "No notice has been delivered yet.")
        self.assertIn("check_alert: Being retried, try 2, SMTPServerDisconnected", check.detail)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_a_backend_that_delivers_nothing_is_said(self):
        check = record.check_mail()
        self.assertEqual(check.status, record.OK)
        self.assertEqual(check.detail, "Nothing reaches anyone: the mail backend is console.")

    def test_it_is_one_of_the_scheduled_checks(self):
        self.assertIn(record.check_mail, record.ALL_CHECKS)

    @override_settings(ALERT_EMAILS=["ops@example.test"])
    def test_the_alert_run_mails_the_warning(self):
        Platform.objects.create(site_name="Zenobia Test", author="Tests",
                                publication_year=2026, active=True)
        self.give_up(3)
        with mock.patch.object(record, "ALL_CHECKS", (record.check_mail,)):
            alerts.run()
        [alert] = mail.outbox
        self.assertEqual(alert.subject, "Zenobia Test: Mail needs attention")
        self.assertIn("The last 3 notices could not be delivered.", alert.body)
        # That mail left, so the way out works again, and the next run says so.
        with mock.patch.object(record, "ALL_CHECKS", (record.check_mail,)):
            alerts.run()
        self.assertEqual(mail.outbox[1].extra_headers["X-Toto-Notice"], "check_recovered")
