"""The record checks on a schedule, and the mail they send (2026-10-01).

``toto.monit.alerts.run`` is fed made-up verdicts at made-up times; the mail
goes through ``toto.core.notices.send_notice`` into Django's locmem outbox.
"""

from __future__ import annotations

import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.core import mail
from django.core.cache import cache
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

    def test_recovery_is_mailed_once_it_has_held(self):
        self.at(0, verdict(record.FAIL))
        self.at(5, verdict(record.OK, summary="Newest 2 h ago."))
        self.at(30, verdict(record.OK, summary="Newest 2 h ago."))
        self.assertEqual(len(mail.outbox), 1, "back for 25 minutes: not yet")
        self.at(35, verdict(record.OK, summary="Newest 2 h ago."))
        self.at(40, verdict(record.OK, summary="Newest 2 h ago."))
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
        self.at(35, verdict(record.OK))
        self.at(40, verdict(record.FAIL))
        self.assertEqual(len(mail.outbox), 3)
        self.assertEqual(mail.outbox[2].subject, "Zenobia Test: Backups is failing")

    def test_a_flapping_check_is_mailed_once_not_every_run(self):
        # Failing every other run for two hours (2026-10-01, the review).
        for minutes in range(0, 120, 5):
            self.at(minutes, verdict(record.FAIL if minutes % 10 == 0 else record.OK))
        self.assertEqual(self.subjects(), ["Zenobia Test: Backups is failing"])
        self.assertEqual(CheckState.objects.get(key="backups").alerted_status, record.FAIL)
        # Still flapping six hours on: the reminder, as for any failure.
        self.at(6 * 60, verdict(record.FAIL))
        self.assertEqual(self.subjects()[1:], ["Zenobia Test: Backups is still failing"])
        # And once it holds, the recovery.
        self.at(6 * 60 + 5, verdict(record.OK))
        self.at(6 * 60 + 35, verdict(record.OK))
        self.assertEqual(self.subjects()[2:], ["Zenobia Test: Backups is back to normal"])

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
        checks.assert_called_once_with(scheduled=True)
        self.assertEqual([m.to for m in mail.outbox], [["ops@example.test"]],
                         "a comma-separated string, blanks and repeats dropped")
        self.assertTrue(CheckState.objects.filter(key="backups").exists())

    def test_it_is_on_the_beat_when_monit_is(self):
        from toto.schedules import beat_schedule

        entry = beat_schedule(alerts=True, alerts_minutes=5)["monit-alert-checks"]
        self.assertEqual(entry["task"], "toto.monit.tasks.monit_alert_checks")
        self.assertEqual(entry["schedule"].minute, set(range(0, 60, 5)))
        self.assertNotIn("monit-alert-checks", beat_schedule(monit=True))


LOCAL_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                           "LOCATION": "monit-scheduled-checks"}}


@override_settings(CACHES=LOCAL_CACHE)
class ScheduledFormTests(TestCase):
    """The alert run, every few minutes, does not read the whole media tree
    and audit chain each time; the page still does (2026-10-01, the review)."""

    def setUp(self):
        from toto.audit.services import record as audit

        cache.clear()
        for index in range(5):
            audit(f"MONIT_TEST_{index}", app_label="monit")

    def walks(self):
        from toto.audit import services

        return mock.patch.object(services, "verify_chain", wraps=services.verify_chain)

    def test_the_run_asks_for_the_scheduled_forms(self):
        self.assertEqual(set(record.SCHEDULED_FORMS), {record.check_media, record.check_audit})
        with mock.patch.object(record, "ALL_CHECKS", (record.check_media,)), \
                mock.patch.object(Path, "rglob", side_effect=AssertionError("walked")), \
                tempfile.TemporaryDirectory() as scratch, override_settings(MEDIA_ROOT=scratch):
            [check] = record.run_checks(scheduled=True)
            [page] = record.run_checks()
        self.assertEqual((check.status, check.summary), (record.OK, "Writable."))
        self.assertEqual(page.status, record.UNKNOWN, "the page form walks the tree")

    def test_the_media_store_is_counted_on_the_page_only(self):
        with tempfile.TemporaryDirectory() as scratch, override_settings(MEDIA_ROOT=scratch):
            Path(scratch, "logo.png").write_bytes(b"png")
            with mock.patch.object(Path, "rglob", side_effect=AssertionError("walked")):
                self.assertEqual(record.check_media(scheduled=True).status, record.OK)
            self.assertEqual(record.check_media().summary, "1 file(s), 3 B.")
            self.assertEqual(record.check_media(scheduled=True).status, record.OK)
        with override_settings(MEDIA_ROOT="/nonexistent/monit-media"):
            self.assertEqual(record.check_media(scheduled=True).status, record.FAIL)

    def test_the_chain_is_walked_whole_once_then_from_where_it_ended(self):
        from toto.audit.services import record as audit

        with self.walks() as walk:
            self.assertEqual(record.check_audit(scheduled=True).status, record.OK)
            self.assertIsNone(walk.call_args.kwargs.get("after"))
            audit("MONIT_TEST_NEW", app_label="monit")
            check = record.check_audit(scheduled=True)
            self.assertEqual((check.status, check.summary), (record.OK, "6 record(s) verify."))
            self.assertEqual(walk.call_args.kwargs["after"][0], 5)

    def test_a_new_record_edited_is_found_by_the_next_run(self):
        from toto.audit.models import AuditRecord
        from toto.audit.services import record as audit

        record.check_audit(scheduled=True)
        audit("MONIT_TEST_NEW", app_label="monit")
        AuditRecord.objects.filter(sequence=6).update(object_description="rewritten")
        check = record.check_audit(scheduled=True)
        self.assertEqual((check.status, check.summary), (record.FAIL, "Broken at sequence 6."))

    def test_the_record_it_starts_from_must_keep_its_hash(self):
        from toto.audit.models import AuditRecord

        record.check_audit(scheduled=True)
        AuditRecord.objects.filter(sequence=5).update(record_hash="0" * 64)
        self.assertEqual(record.check_audit(scheduled=True).status, record.FAIL)

    def test_an_old_record_edited_is_found_daily_and_on_the_page_at_once(self):
        from toto.audit.models import AuditRecord

        record.check_audit(scheduled=True)
        AuditRecord.objects.filter(sequence=2).update(object_description="rewritten")
        self.assertEqual(record.check_audit().status, record.FAIL, "the page walks it all")
        self.assertEqual(record.check_audit(scheduled=True).status, record.OK,
                         "between whole walks the run reads only what is new")
        later = record.time.time() + record.AUDIT_WALK_HOURS * 3600
        with mock.patch.object(record.time, "time", return_value=later):
            check = record.check_audit(scheduled=True)
        self.assertEqual((check.status, check.summary), (record.FAIL, "Broken at sequence 2."))

    def test_a_cache_that_forgets_costs_a_whole_walk(self):
        record.check_audit(scheduled=True)
        cache.clear()
        with self.walks() as walk:
            self.assertEqual(record.check_audit(scheduled=True).status, record.OK)
        self.assertIsNone(walk.call_args.kwargs.get("after"))
