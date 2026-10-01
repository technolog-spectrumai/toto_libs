"""Notices on the worker, with retry (2026-10-01).

Where a worker runs, ``toto.core.notices.send_notice`` queues the rendered
notice once the change that caused it commits; ``tasks.deliver_notice`` tries
``TRIES`` times, ``RETRY_DELAYS`` apart, and gives up. Every try leaves its
outcome on ``NoticeDelivery`` — never the address, the mail, a token or the
SMTP password. Without a worker the notice is sent at once, one try.
"""

from __future__ import annotations

import smtplib
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.db import transaction
from django.db.transaction import TransactionManagementError
from django.forms.models import model_to_dict
from django.test import TestCase, override_settings
from kombu.exceptions import OperationalError

from toto.core import notices
from toto.core.models import NoticeDelivery, Platform
from toto.core.notices import send_notice
from toto.core.tasks import deliver_notice

User = get_user_model()
LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
SEND = "django.core.mail.EmailMessage.send"
TASK = "toto.core.tasks.deliver_notice"


def alert(**over):
    message = {"kind": "check_alert", "to": "ops@example.test",
               "subject": "Zenobia Test: Backups is failing", "body": "Newest 3 days ago."}
    message.update(over)
    return message


def refused():
    # An SMTP server's own words can echo the login and the address.
    return smtplib.SMTPAuthenticationError(
        535, b"5.7.8 ops@example.test: authentication failed for hunter2")


def outcome(kind="check_alert"):
    return NoticeDelivery.objects.get(purpose=kind)


@override_settings(EMAIL_BACKEND=LOCMEM)
class NoticeTestCase(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Zenobia Test", author="Tests",
                                publication_year=2026, active=True)
        self.user = User.objects.create_user("ada", "ada@example.test", "pw")


@override_settings(NOTICES_VIA_WORKER=True)
class QueuedTests(NoticeTestCase):
    def test_the_notice_is_queued_once_the_change_commits(self):
        with mock.patch(TASK) as task:
            with self.captureOnCommitCallbacks() as callbacks:
                self.assertTrue(send_notice(self.user, "password_changed",
                                            {"address": "203.0.113.7"}))
                task.delay.assert_not_called()
            for callback in callbacks:
                callback()
        [(message,), _kwargs] = task.delay.call_args
        self.assertEqual(message["kind"], "password_changed")
        self.assertEqual(message["to"], "ada@example.test")
        self.assertEqual(message["subject"], "Zenobia Test: your password was changed")
        self.assertIn("203.0.113.7", message["body"])
        # Rendered here, sent there: nothing has left yet.
        self.assertEqual(mail.outbox, [])
        self.assertFalse(NoticeDelivery.objects.exists())

    def test_nothing_is_queued_for_a_change_that_rolls_back(self):
        with mock.patch(TASK) as task:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                with self.assertRaises(RuntimeError):
                    with transaction.atomic():
                        self.assertTrue(send_notice(self.user, "password_changed"))
                        raise RuntimeError("the change did not happen")
        self.assertEqual(callbacks, [])
        task.delay.assert_not_called()
        self.assertEqual(mail.outbox, [])

    def test_a_broker_that_cannot_be_reached_sends_it_at_once(self):
        with mock.patch(TASK) as task:
            task.delay.side_effect = OperationalError("Error 111 connecting to redis:6379")
            with self.captureOnCommitCallbacks(execute=True):
                self.assertTrue(send_notice(self.user, "password_changed"))
        [notice] = mail.outbox
        self.assertEqual(notice.to, ["ada@example.test"])
        self.assertEqual(outcome("password_changed").status, NoticeDelivery.SENT)


    def test_a_transaction_that_cannot_wait_for_a_commit_sends_it_at_once(self):
        with mock.patch(TASK) as task, \
                mock.patch("toto.core.notices.transaction.on_commit",
                           side_effect=TransactionManagementError("manual")):
            self.assertTrue(send_notice(self.user, "password_changed"))
        task.delay.assert_not_called()
        self.assertEqual(len(mail.outbox), 1)


@override_settings(NOTICES_VIA_WORKER=False)
class AtOnceTests(NoticeTestCase):
    def test_without_a_worker_it_is_sent_at_once_and_recorded(self):
        self.assertTrue(send_notice(self.user, "password_changed"))
        [notice] = mail.outbox
        self.assertEqual(notice.extra_headers[notices.PURPOSE_HEADER], "password_changed")
        row = outcome("password_changed")
        self.assertEqual((row.status, row.tries, row.failures, row.error),
                         (NoticeDelivery.SENT, 1, 0, ""))
        self.assertIsNotNone(row.sent_at)
        self.assertEqual(row.recipient_hash, notices.recipient_hash("ada@example.test"))
        self.assertNotIn("ada", row.recipient_hash)

    def test_a_refusal_is_one_try_and_a_failure(self):
        with mock.patch(SEND, side_effect=refused()):
            self.assertFalse(send_notice(self.user, "password_changed"))
        row = outcome("password_changed")
        self.assertEqual((row.status, row.tries, row.failures, row.error),
                         (NoticeDelivery.FAILED, 1, 1, "SMTPAuthenticationError (535)"))

    def test_a_template_that_does_not_render_is_a_failed_send(self):
        with mock.patch("toto.core.notices.loader.render_to_string",
                        side_effect=RuntimeError("boom")):
            self.assertFalse(send_notice(self.user, "password_changed"))
        row = outcome("password_changed")
        self.assertEqual((row.status, row.failures, row.error),
                         (NoticeDelivery.FAILED, 1, "RuntimeError"))


class RetryTests(NoticeTestCase):
    def test_a_failing_server_is_tried_again_until_it_takes_the_mail(self):
        with mock.patch(SEND, side_effect=[smtplib.SMTPServerDisconnected("gone"),
                                           ConnectionRefusedError(111, "Connection refused"),
                                           1]) as send:
            result = deliver_notice.apply(args=[alert()])
        self.assertEqual(send.call_count, 3)
        self.assertEqual(result.result, {"kind": "check_alert", "status": "sent", "tries": 3})
        row = outcome()
        self.assertEqual((row.status, row.tries, row.failures, row.error),
                         (NoticeDelivery.SENT, 3, 0, ""))

    def test_a_try_that_fails_is_recorded_as_being_retried(self):
        seen = []

        def send(*args, **kwargs):
            row = NoticeDelivery.objects.filter(purpose="check_alert").first()
            seen.append(row and (row.status, row.tries, row.error))
            raise ConnectionRefusedError(111, "Connection refused")

        with mock.patch(SEND, side_effect=send):
            deliver_notice.apply(args=[alert()])
        error = "ConnectionRefusedError: Connection refused"
        self.assertEqual(seen, [None] + [(NoticeDelivery.RETRYING, n, error)
                                         for n in range(1, notices.TRIES)])

    def test_it_gives_up_after_five_tries_and_records_the_failure(self):
        with mock.patch(SEND, side_effect=refused()) as send:
            result = deliver_notice.apply(args=[alert()])
        self.assertEqual(notices.TRIES, 5)
        self.assertEqual(send.call_count, 5)
        self.assertEqual(result.result, {"kind": "check_alert", "status": "failed", "tries": 5})
        row = outcome()
        self.assertEqual((row.status, row.tries, row.failures, row.error),
                         (NoticeDelivery.FAILED, 5, 1, "SMTPAuthenticationError (535)"))
        self.assertIsNone(row.sent_at)

    def test_it_waits_longer_each_time_about_an_hour_in_all(self):
        with mock.patch(SEND, side_effect=refused()), \
                mock.patch.object(deliver_notice, "retry", wraps=deliver_notice.retry) as retry:
            deliver_notice.apply(args=[alert()])
        self.assertEqual([call.kwargs["countdown"] for call in retry.call_args_list],
                         [60, 300, 900, 2400])
        self.assertTrue(55 * 60 <= sum(notices.RETRY_DELAYS) <= 65 * 60)
        # What the retry carries into the worker's log: the kind and the class.
        self.assertEqual(str(retry.call_args.kwargs["exc"]),
                         "check_alert: SMTPAuthenticationError (535)")

    def test_failures_add_up_across_kinds_until_one_is_delivered(self):
        with mock.patch(SEND, side_effect=refused()):
            deliver_notice.apply(args=[alert()])
            deliver_notice.apply(args=[alert()])
            deliver_notice.apply(args=[alert(kind="new_sign_in", to="ada@example.test")])
        self.assertEqual(sorted(NoticeDelivery.objects.values_list("purpose", "failures")),
                         [("check_alert", 2), ("new_sign_in", 1)])
        with mock.patch(SEND, return_value=1):
            deliver_notice.apply(args=[alert(kind="password_changed", to="ada@example.test")])
        self.assertEqual(sorted(NoticeDelivery.objects.values_list("purpose", "failures")),
                         [("check_alert", 0), ("new_sign_in", 0), ("password_changed", 0)])
        # The last outcome of each kind stays what it was.
        self.assertEqual(outcome().status, NoticeDelivery.FAILED)


@override_settings(EMAIL_HOST_PASSWORD="hunter2")
class NothingSecretKeptTests(NoticeTestCase):
    def test_the_outcome_the_log_and_the_result_hold_no_address_mail_or_secret(self):
        message = {"kind": "email_change_confirm", "to": "New.Address@example.test",
                   "subject": "Confirm your new address",
                   "body": "https://zenobia.example.org/account/email/confirm/?token=Tok3nTok3n"}
        refusal = smtplib.SMTPRecipientsRefused(
            {"New.Address@example.test": (550, b"5.1.1 New.Address@example.test unknown "
                                               b"(login ops, password hunter2)")})
        # Every logger: the worker's own lines (retry, succeeded) included.
        with mock.patch(SEND, side_effect=refusal):
            with self.assertLogs(level="INFO") as logs:
                result = deliver_notice.apply(args=[message])
        row = outcome("email_change_confirm")
        self.assertEqual(row.error, "SMTPRecipientsRefused (550)")
        # The hash ignores case, as mail servers do.
        self.assertEqual(row.recipient_hash, notices.recipient_hash("new.address@EXAMPLE.test"))
        kept = " ".join(str(value) for value in model_to_dict(row).values())
        for secret in ("New.Address", "new.address", "example.test", "Tok3nTok3n",
                       "Confirm your", "hunter2"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, kept)
                self.assertNotIn(secret, "\n".join(logs.output))
                self.assertNotIn(secret, str(result.result))

    def test_what_an_error_says_is_its_class_and_code(self):
        text = notices.error_text
        self.assertEqual(text(smtplib.SMTPDataError(554, b"rejected ops@example.test")),
                         "SMTPDataError (554)")
        self.assertEqual(text(smtplib.SMTPServerDisconnected("ops@example.test")),
                         "SMTPServerDisconnected")
        self.assertEqual(text(OSError(101, "Network is unreachable")),
                         "OSError: Network is unreachable")
        self.assertEqual(text(ConnectionRefusedError("ops@example.test")),
                         "ConnectionRefusedError")
        self.assertEqual(text(ValueError("ops@example.test")), "ValueError")
        # A refusal shaped unlike smtplib's still answers, with its class.
        self.assertEqual(text(smtplib.SMTPRecipientsRefused({"ops@example.test": 550})),
                         "SMTPRecipientsRefused")
