"""The record checks on a schedule, and the mail they send (2026-10-01).

``toto.monit.alerts.run`` is fed made-up verdicts at made-up times; the mail
goes through ``toto.core.notices.send_notice`` into Django's locmem outbox.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

from django.core import mail
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone

from toto.core.models import Platform
from toto.monit import alerts, record, tasks
from toto.monit.models import CheckState

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"


def verdict(status, key="backups", label="Backups", summary="Newest 3 days ago."):
    return record.Check(key, label, status, summary, detail="/app/backups/db")


@override_settings(EMAIL_BACKEND=LOCMEM, ALERT_EMAILS=["ops@example.test"],
                   ALERT_REMIND_HOURS=6, PLATFORM_DOMAIN="zenobia.example.org")
class AlertRunTests(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Zenobia Test", author="Tests",
                                publication_year=2026, active=True)
        self.start = timezone.now()

    def at(self, minutes, *verdicts):
        # The mail follows the run's commit; a test runs inside a
        # transaction, so the commit is played here.
        with self.captureOnCommitCallbacks(execute=True):
            sent = alerts.run(now=self.start + timedelta(minutes=minutes),
                              checks=list(verdicts))
        return sent

    def subjects(self):
        return [message.subject for message in mail.outbox]

    def test_a_check_that_goes_bad_is_mailed_once(self):
        self.at(0, verdict(record.OK))
        self.assertEqual(mail.outbox, [])
        self.at(5, verdict(record.FAIL))
        self.at(10, verdict(record.FAIL))
        [alert] = mail.outbox
        self.assertEqual(alert.to, ["ops@example.test"])
        self.assertEqual(alert.subject, "Zenobia Test: Backups is failing")
        self.assertEqual(alert.extra_headers["X-Toto-Notice"], "check_alert")
        self.assertIn('The check "Backups" is failing.', alert.body)
        self.assertIn("Newest 3 days ago.", alert.body)
        self.assertIn("/app/backups/db", alert.body)
        self.assertIn("a reminder follows every 6 hours", alert.body)
        self.assertIn("https://zenobia.example.org/monit/status/", alert.body)
        self.assertIn("so no mail can tell you that the server is down", alert.body)

    def test_staying_failed_is_quiet_until_the_reminder_is_due(self):
        self.at(0, verdict(record.FAIL))
        self.at(6 * 60 - 5, verdict(record.FAIL))
        self.assertEqual(len(mail.outbox), 1)
        self.at(6 * 60, verdict(record.FAIL))
        self.assertEqual(self.subjects()[1], "Zenobia Test: Backups is still failing")
        # The clock starts again from the reminder.
        self.at(6 * 60 + 5, verdict(record.FAIL))
        self.at(12 * 60 - 5, verdict(record.FAIL))
        self.assertEqual(len(mail.outbox), 2)
        self.at(12 * 60, verdict(record.FAIL))
        self.assertEqual(len(mail.outbox), 3)

    @override_settings(ALERT_REMIND_HOURS=0)
    def test_no_reminders_when_the_interval_is_zero(self):
        self.at(0, verdict(record.FAIL))
        self.at(48 * 60, verdict(record.FAIL))
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn("reminder", mail.outbox[0].body)

    def test_recovery_is_mailed_once(self):
        self.at(0, verdict(record.FAIL))
        self.at(5, verdict(record.OK, summary="Newest 2 h ago."))
        self.at(10, verdict(record.OK, summary="Newest 2 h ago."))
        self.assertEqual(self.subjects(), ["Zenobia Test: Backups is failing",
                                           "Zenobia Test: Backups is back to normal"])
        recovered = mail.outbox[1]
        self.assertEqual(recovered.extra_headers["X-Toto-Notice"], "check_recovered")
        self.assertIn("Newest 2 h ago.", recovered.body)
        self.assertIn("It had needed attention since", recovered.body)
        self.assertEqual(CheckState.objects.get(key="backups").alerted_status, "")

    def test_failing_again_after_a_recovery_is_a_new_alert(self):
        self.at(0, verdict(record.FAIL))
        self.at(5, verdict(record.OK))
        self.at(10, verdict(record.FAIL))
        self.assertEqual(len(mail.outbox), 3)
        self.assertEqual(mail.outbox[2].subject, "Zenobia Test: Backups is failing")

    def test_a_warning_is_said_once_and_never_repeated(self):
        self.at(0, verdict(record.WARN))
        self.at(7 * 60, verdict(record.WARN))
        self.assertEqual(self.subjects(), ["Zenobia Test: Backups needs attention"])
        self.assertNotIn("reminder", mail.outbox[0].body)

    def test_worse_is_mailed_better_is_not(self):
        self.at(0, verdict(record.WARN))
        self.at(5, verdict(record.FAIL))
        self.at(10, verdict(record.WARN))
        self.at(15, verdict(record.FAIL))
        self.assertEqual(self.subjects(), ["Zenobia Test: Backups needs attention",
                                           "Zenobia Test: Backups is failing"])

    def test_a_probe_that_cannot_run_is_mailed_once(self):
        self.at(0, verdict(record.UNKNOWN, summary="Could not be checked."))
        self.at(7 * 60, verdict(record.UNKNOWN, summary="Could not be checked."))
        self.assertEqual(self.subjects(), ["Zenobia Test: Backups could not be checked"])

    def test_off_is_not_a_problem(self):
        self.at(0, verdict(record.OFF))
        self.at(5, verdict(record.OK))
        self.at(10, verdict(record.OFF))
        self.assertEqual(mail.outbox, [])

    def test_each_check_is_its_own_mail(self):
        self.at(0, verdict(record.FAIL), verdict(record.WARN, key="disk", label="Disk"))
        self.assertEqual(sorted(self.subjects()), ["Zenobia Test: Backups is failing",
                                                   "Zenobia Test: Disk needs attention"])

    @override_settings(ALERT_EMAILS=["ops@example.test", "oncall@example.test"])
    def test_every_address_gets_its_own_mail(self):
        self.at(0, verdict(record.FAIL))
        self.assertEqual(sorted(m.to[0] for m in mail.outbox),
                         ["oncall@example.test", "ops@example.test"])

    def test_no_address_no_mail_but_the_state_is_kept(self):
        with override_settings(ALERT_EMAILS=[]):
            self.at(0, verdict(record.FAIL))
        self.assertEqual(mail.outbox, [])
        state = CheckState.objects.get(key="backups")
        self.assertEqual((state.status, state.alerted_status), (record.FAIL, ""))
        # An address added while it is still failing is told then.
        self.at(5, verdict(record.FAIL))
        self.assertEqual(self.subjects(), ["Zenobia Test: Backups is failing"])

    def test_a_mail_no_address_took_is_tried_again(self):
        with mock.patch("django.core.mail.EmailMessage.send",
                        side_effect=ConnectionRefusedError("smtp down")):
            with self.assertLogs("toto.monit.alerts", "WARNING"):
                self.at(0, verdict(record.FAIL))
        self.assertEqual(CheckState.objects.get(key="backups").alerted_status, "")
        self.at(5, verdict(record.FAIL))
        self.assertEqual(self.subjects(), ["Zenobia Test: Backups is failing"])

    @override_settings(NOTICES_VIA_WORKER=True)
    def test_with_a_worker_the_mail_is_queued_once_the_run_commits(self):
        with mock.patch("toto.core.tasks.deliver_notice") as task:
            with self.captureOnCommitCallbacks(execute=True):
                alerts.run(now=self.start, checks=[verdict(record.FAIL)])
                task.delay.assert_not_called()
        [(message,), _kwargs] = task.delay.call_args
        self.assertEqual((message["kind"], message["to"]), ("check_alert", "ops@example.test"))
        self.assertEqual(message["subject"], "Zenobia Test: Backups is failing")
        self.assertEqual(mail.outbox, [])
        # Queued is mailed: the worker retries it, the next run does not.
        self.assertEqual(CheckState.objects.get(key="backups").alerted_status, record.FAIL)

    @override_settings(NOTICES_VIA_WORKER=True)
    def test_a_run_that_fails_queues_nothing(self):
        with mock.patch("toto.core.tasks.deliver_notice") as task, \
                mock.patch.object(CheckState, "save", side_effect=DatabaseError("gone")):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                with self.assertRaises(DatabaseError):
                    self.at(0, verdict(record.FAIL))
        self.assertEqual(callbacks, [])
        task.delay.assert_not_called()

    def test_sent_at_once_the_mail_still_waits_for_the_commit(self):
        # Without a worker too (2026-10-01, the review): nothing leaves
        # while the run's transaction is open.
        with self.captureOnCommitCallbacks() as callbacks:
            sent = alerts.run(now=self.start, checks=[verdict(record.FAIL)])
            self.assertEqual(mail.outbox, [])
        self.assertEqual(CheckState.objects.get(key="backups").alerted_status, record.FAIL)
        for callback in callbacks:
            callback()
        self.assertEqual(self.subjects(), ["Zenobia Test: Backups is failing"])
        self.assertEqual(sent[alerts.PROBLEM], 1)

    def test_a_run_that_fails_mails_nothing_at_once_either(self):
        with mock.patch.object(CheckState, "save", side_effect=DatabaseError("gone")):
            with self.captureOnCommitCallbacks(execute=True):
                with self.assertRaises(DatabaseError):
                    alerts.run(now=self.start, checks=[verdict(record.FAIL)])
        self.assertEqual(mail.outbox, [])
        # So the next run, which saves, mails it — once.
        self.at(5, verdict(record.FAIL))
        self.at(10, verdict(record.FAIL))
        self.assertEqual(self.subjects(), ["Zenobia Test: Backups is failing"])

    def test_the_state_follows_the_verdict(self):
        self.at(0, verdict(record.OK))
        self.at(5, verdict(record.FAIL))
        state = CheckState.objects.get(key="backups")
        self.assertEqual(state.status, record.FAIL)
        self.assertEqual(state.since, self.start + timedelta(minutes=5))
        self.assertEqual(state.bad_since, self.start + timedelta(minutes=5))
        self.assertEqual(state.alerted_at, self.start + timedelta(minutes=5))
        self.at(10, verdict(record.FAIL))
        self.assertEqual(CheckState.objects.get(key="backups").since,
                         self.start + timedelta(minutes=5))


@override_settings(EMAIL_BACKEND=LOCMEM, ALERT_EMAILS="ops@example.test, ,ops@example.test")
class TaskTests(TestCase):
    def test_the_task_runs_the_record_checks_and_mails(self):
        with mock.patch.object(record, "run_checks",
                               return_value=[verdict(record.FAIL)]) as checks, \
                self.captureOnCommitCallbacks(execute=True):
            tasks.monit_alert_checks()
        checks.assert_called_once_with()
        self.assertEqual([m.to for m in mail.outbox], [["ops@example.test"]],
                         "a comma-separated string, blanks and repeats dropped")
        self.assertTrue(CheckState.objects.filter(key="backups").exists())

    def test_it_is_on_the_beat_when_monit_is(self):
        from toto.schedules import beat_schedule

        entry = beat_schedule(alerts=True, alerts_minutes=5)["monit-alert-checks"]
        self.assertEqual(entry["task"], "toto.monit.tasks.monit_alert_checks")
        self.assertEqual(entry["schedule"].minute, set(range(0, 60, 5)))
        self.assertNotIn("monit-alert-checks", beat_schedule(monit=True))
