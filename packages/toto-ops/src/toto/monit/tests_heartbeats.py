"""Heartbeats and run records for every beat entry (2026-10-01).

Real Celery tasks run eagerly (``Task.apply``), so the run records come from
the same signals a worker sends; the overdue verdicts are fed runs at made-up
ages against a made-up schedule.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

from celery import shared_task, signals
from celery.exceptions import TimeLimitExceeded
from celery.schedules import crontab, schedule
from django.apps import apps as django_apps
from django.conf import settings
from django.core import mail
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from toto.core.models import Platform
from toto.monit import alerts, heartbeats, jobs, record, tasks
from toto.monit.models import BeatEntry, Snapshot, TaskRun

LEVY = "toto.monit.tests_heartbeats.levy"
BROKEN = "toto.monit.tests_heartbeats.broken"
PAYOUT = "toto.monit.tests_heartbeats.payout"


@shared_task(name=LEVY, bind=True)
def levy(self):
    # What the run's own row says while it runs: the start is written first.
    seen = TaskRun.objects.get(task_id=self.request.id).status
    return [{"metric_code": "storage", "day": "2026-10-01", "skipped_reason": "",
             "counts": {"levied": 3, "nothing_held": 10}, "while_running": seen}]


@shared_task(name=BROKEN)
def broken():
    raise ValueError("the ledger is shut")


@shared_task(name=PAYOUT)
def payout():
    return {"paid": 1}


SCHEDULE = {
    "tax-daily-levy": {"task": LEVY, "schedule": crontab(hour=4, minute=15)},
    "broken-hourly": {"task": BROKEN, "schedule": crontab(minute="17")},
}


class CadenceTests(SimpleTestCase):
    def test_crontabs_give_their_widest_gap(self):
        cases = [
            (crontab(), 60),
            (crontab(minute="*/5"), 300),
            (crontab(minute="17"), 3600),
            (crontab(hour=4, minute=15), 86400),
            # Every seven minutes is seven, not the four across the hour.
            (crontab(minute="*/7"), 420),
            # Office hours: the sixteen hours overnight.
            (crontab(hour="9-17", minute=0), 16 * 3600),
            # Weekdays: Friday morning to Monday morning.
            (crontab(day_of_week="mon-fri", hour=6, minute=0), 3 * 86400),
            # The first of the month: the widest month.
            (crontab(day_of_month=1, hour=0, minute=0), 31 * 86400),
        ]
        for cron, seconds in cases:
            with self.subTest(cron=cron):
                self.assertEqual(heartbeats.cadence_seconds(cron), seconds)

    def test_intervals_are_their_length(self):
        self.assertEqual(heartbeats.cadence_seconds(120.0), 120)
        self.assertEqual(heartbeats.cadence_seconds(timedelta(minutes=3)), 180)
        self.assertEqual(heartbeats.cadence_seconds(schedule(timedelta(minutes=10))), 600)

    def test_what_cannot_be_read_is_none(self):
        for value in (None, 0, -5, True, "*/5", crontab(day_of_month=30, month_of_year=2)):
            with self.subTest(value=value):
                self.assertIsNone(heartbeats.cadence_seconds(value))

    def test_every_entry_of_this_hosts_schedule_has_a_cadence(self):
        for name, entry in settings.CELERY_BEAT_SCHEDULE.items():
            with self.subTest(entry=name):
                self.assertTrue(heartbeats.cadence_seconds(entry["schedule"]))

    def test_spans_read_as_words(self):
        self.assertEqual(heartbeats.span(300), "5\xa0minutes")
        self.assertEqual(heartbeats.span(86400), "1\xa0day")
        self.assertEqual(heartbeats.span(30), "30 seconds")
        self.assertEqual(heartbeats.span(None), "—")


class SummaryTests(SimpleTestCase):
    def test_counts_read_as_one_line_and_empty_values_are_left_out(self):
        self.assertEqual(
            heartbeats.summarize({"label": "2026-10-01T04", "paid": 3, "skipped": 0,
                                  "failures": []}),
            "label=2026-10-01T04, paid=3, skipped=0")
        self.assertEqual(heartbeats.summarize(None), "")

    def test_nothing_secret_is_kept(self):
        text = heartbeats.summarize({"paid": 3, "api_token": "abcdef123456",
                                     "broker": "redis://:hunter2secret@redis:6379/0"})
        self.assertNotIn("abcdef123456", text)
        self.assertNotIn("hunter2secret", text)
        self.assertIn("paid=3", text)

    @override_settings(MONETARY_ISSUER_KEY="a-very-secret-issuer-value")
    def test_a_secret_settings_value_is_starred_wherever_it_appears(self):
        self.assertNotIn("a-very-secret-issuer-value",
                         heartbeats.summarize(["copied a-very-secret-issuer-value"]))
        self.assertNotIn("a-very-secret-issuer-value",
                         heartbeats.describe(RuntimeError("bad a-very-secret-issuer-value")))

    def test_long_values_are_cut(self):
        text = heartbeats.summarize({"rows": list(range(1000)), "note": "x" * 1000})
        self.assertLessEqual(len(text), heartbeats.SUMMARY_LENGTH)
        self.assertTrue(text.endswith("…"))


@override_settings(CELERY_BEAT_SCHEDULE=SCHEDULE)
class RunRecordTests(TestCase):
    def test_a_successful_run_is_recorded_with_its_summary(self):
        result = levy.apply()
        run = TaskRun.objects.get(task_id=result.id)
        self.assertEqual(run.task, LEVY)
        self.assertEqual(run.status, heartbeats.SUCCESS)
        self.assertIsNotNone(run.finished_at)
        self.assertGreaterEqual(run.finished_at, run.started_at)
        self.assertEqual(run.summary, "metric_code=storage, day=2026-10-01, "
                                      "counts={levied=3, nothing_held=10}, "
                                      "while_running=running")
        self.assertEqual(run.error, "")

    def test_a_failed_run_is_recorded_with_its_error(self):
        result = broken.apply()
        run = TaskRun.objects.get(task_id=result.id)
        self.assertEqual(run.status, heartbeats.FAILED)
        self.assertEqual(run.error, "ValueError: the ledger is shut")
        self.assertIsNotNone(run.finished_at)

    def test_a_task_the_schedule_does_not_name_leaves_no_record(self):
        payout.apply()
        self.assertFalse(TaskRun.objects.exists())

    def test_a_failure_the_worker_process_saw_closes_the_run(self):
        """A child killed by the hard time limit never reaches its postrun;
        the worker process's task_failure is the only word the run gets."""
        run = TaskRun.objects.create(task=LEVY, task_id="killed-1")
        signals.task_failure.send(sender=levy, task_id="killed-1",
                                  exception=TimeLimitExceeded(3600))
        run.refresh_from_db()
        self.assertEqual(run.status, heartbeats.FAILED)
        self.assertIn("TimeLimitExceeded", run.error)
        self.assertIsNotNone(run.finished_at)

    def test_a_record_that_cannot_be_written_never_breaks_the_task(self):
        scheduled = {**SCHEDULE, "payout": {"task": PAYOUT, "schedule": 3600.0}}
        with override_settings(CELERY_BEAT_SCHEDULE=scheduled), \
             mock.patch.object(TaskRun.objects, "update_or_create",
                               side_effect=RuntimeError("database gone")), \
             self.assertLogs("toto.monit.heartbeats", "WARNING"):
            result = payout.apply()
        self.assertEqual(result.state, "SUCCESS")
        self.assertEqual(result.result, {"paid": 1})
        # The start could not be written; the end still is, with its start.
        run = TaskRun.objects.get(task_id=result.id)
        self.assertEqual(run.status, heartbeats.SUCCESS)
        self.assertEqual(run.summary, "paid=1")
        self.assertLessEqual(run.started_at, run.finished_at)

    def test_beat_start_notes_every_entry_and_keeps_the_first_sighting(self):
        signals.beat_init.send(sender=None)
        first = {row.name: row.first_seen for row in BeatEntry.objects.all()}
        self.assertEqual(set(first), set(SCHEDULE))
        later = timezone.now() + timedelta(hours=1)
        with mock.patch("django.utils.timezone.now", return_value=later):
            signals.beat_init.send(sender=None)
        for row in BeatEntry.objects.all():
            self.assertEqual(row.first_seen, first[row.name])
            self.assertEqual(row.last_seen, later)
        self.assertEqual(heartbeats.beat_started(), later)


FIVE, HOURLY, DAILY = "t.five", "t.hourly", "t.daily"
WATCHED = {
    "every-five": {"task": FIVE, "schedule": crontab(minute="*/5")},
    "hourly": {"task": HOURLY, "schedule": crontab(minute="17")},
    "daily": {"task": DAILY, "schedule": crontab(hour=4, minute=15)},
}
GRACE = timedelta(seconds=heartbeats.GRACE_SECONDS)


@override_settings(CELERY_BEAT_SCHEDULE=WATCHED)
class OverdueTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def ran(self, task, ago, status=heartbeats.SUCCESS):
        started = self.now - ago
        return TaskRun.objects.create(task=task, task_id=f"{task}-{ago}",
                                      status=status, started_at=started,
                                      finished_at=started + timedelta(seconds=2))

    def fresh(self, *tasks):
        for task in tasks:
            self.ran(task, timedelta(minutes=1))

    def states(self):
        return {state.name: state.status for state in heartbeats.entries(now=self.now)}

    def test_entries_that_ran_on_time_pass(self):
        self.fresh(FIVE, HOURLY, DAILY)
        check = record.check_overdue()
        self.assertEqual(check.status, record.OK)
        self.assertEqual(check.summary, "3 scheduled tasks, none overdue.")
        self.assertEqual(check.key, "overdue")

    def test_twice_the_cadence_and_the_grace_is_a_warning_three_times_a_failure(self):
        self.fresh(FIVE, HOURLY)
        day = timedelta(days=1)
        self.ran(DAILY, 2 * day + GRACE - timedelta(minutes=1))
        self.assertEqual(self.states()["daily"], record.OK, "inside the grace")

        TaskRun.objects.filter(task=DAILY).delete()
        self.ran(DAILY, 2 * day + GRACE + timedelta(minutes=1))
        self.assertEqual(self.states()["daily"], record.WARN)
        check = record.check_overdue()
        self.assertEqual(check.status, record.WARN)
        self.assertEqual(check.summary, "1 of 3 scheduled tasks is overdue: daily.")
        self.assertIn("daily: last started 2\xa0days", check.detail)
        self.assertIn("every 1\xa0day", check.detail)

        TaskRun.objects.filter(task=DAILY).delete()
        self.ran(DAILY, 3 * day + GRACE + timedelta(minutes=1))
        self.assertEqual(self.states()["daily"], record.FAIL)
        self.assertEqual(record.check_overdue().status, record.FAIL)

    def test_a_failed_run_still_counts_as_a_heartbeat(self):
        self.fresh(FIVE, HOURLY)
        self.ran(DAILY, timedelta(hours=3), status=heartbeats.FAILED)
        self.assertEqual(self.states()["daily"], record.OK)

    def test_nothing_started_in_the_busiest_window_is_a_dead_beat(self):
        hours = timedelta(hours=4)
        self.ran(FIVE, hours)
        self.ran(HOURLY, hours)
        self.ran(DAILY, hours)
        BeatEntry.objects.create(name="every-five", task=FIVE,
                                 first_seen=self.now - timedelta(days=9),
                                 last_seen=self.now - timedelta(days=2))
        check = record.check_overdue()
        self.assertEqual(check.status, record.FAIL, "the hourly one is 4 h late")
        self.assertIn("Nothing scheduled has started for 4\xa0hours", check.summary)
        self.assertIn("celery beat is not running, or no worker takes its tasks",
                      check.summary)
        self.assertIn("Beat last started", check.detail)

    def test_every_entry_overdue_says_beat_is_dead(self):
        for task in (FIVE, HOURLY, DAILY):
            self.ran(task, timedelta(days=4))
        check = record.check_overdue()
        self.assertEqual(check.status, record.FAIL)
        self.assertEqual(check.summary, "Every scheduled task is overdue: celery beat "
                                        "is not running, or no worker takes its tasks.")
        self.assertIn("No start of celery beat has been recorded.", check.detail)
        self.assertEqual(check.value, "3")

    def test_one_stalled_entry_among_running_ones_is_named_not_called_a_dead_beat(self):
        self.fresh(FIVE, DAILY)
        self.ran(HOURLY, timedelta(hours=5))
        check = record.check_overdue()
        self.assertEqual(check.status, record.FAIL)
        self.assertEqual(check.summary, "1 of 3 scheduled tasks is overdue: hourly.")

    def test_a_never_run_entry_counts_from_when_beat_first_had_it(self):
        self.fresh(FIVE, HOURLY)
        BeatEntry.objects.create(name="daily", task=DAILY,
                                 first_seen=self.now - timedelta(hours=1))
        self.assertEqual(self.states()["daily"], record.OK, "new, not yet due")

        BeatEntry.objects.filter(name="daily").update(
            first_seen=self.now - timedelta(days=3) - GRACE - timedelta(minutes=1))
        self.assertEqual(self.states()["daily"], record.FAIL)
        self.assertIn("daily: never started, scheduled since", record.check_overdue().detail)

    def test_a_run_from_before_the_entry_was_scheduled_does_not_make_it_late(self):
        self.fresh(FIVE, HOURLY)
        self.ran(DAILY, timedelta(days=10))
        BeatEntry.objects.create(name="daily", task=DAILY,
                                 first_seen=self.now - timedelta(hours=1))
        self.assertEqual(self.states()["daily"], record.OK)

    def test_without_any_record_the_age_counts_from_when_recording_began(self):
        with mock.patch.object(heartbeats, "recording_began",
                               return_value=self.now - timedelta(hours=1)):
            self.assertEqual(self.states(), {"every-five": record.FAIL,
                                             "hourly": record.OK, "daily": record.OK})
        # On this test database the tables are minutes old: nothing is due.
        self.assertIsNotNone(heartbeats.recording_began())

    @override_settings(CELERY_BEAT_SCHEDULE={})
    def test_nothing_scheduled_is_off(self):
        self.assertEqual(record.check_overdue().status, record.OFF)

    def test_the_check_is_one_of_the_record_checks(self):
        self.assertIn(record.check_overdue, record.ALL_CHECKS)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                       ALERT_EMAILS=["ops@example.test"])
    def test_the_alert_run_mails_an_overdue_entry(self):
        Platform.objects.create(site_name="Zenobia Test", author="Tests",
                                publication_year=2026, active=True)
        self.fresh(FIVE, DAILY)
        self.ran(HOURLY, timedelta(hours=5))
        with mock.patch.object(record, "ALL_CHECKS", (record.check_overdue,)):
            alerts.run()
        [alert] = mail.outbox
        self.assertEqual(alert.subject, "Zenobia Test: Scheduled tasks is failing")
        self.assertIn("1 of 3 scheduled tasks is overdue: hourly.", alert.body)
        self.assertIn("hourly: last started 5\xa0hours ago", alert.body)


@override_settings(CELERY_BEAT_SCHEDULE=WATCHED)
class PruneTests(TestCase):
    def run_at(self, task, days_ago):
        return TaskRun.objects.create(
            task=task, task_id=f"{task}-{days_ago}-{TaskRun.objects.count()}",
            status=heartbeats.SUCCESS,
            started_at=timezone.now() - timedelta(days=days_ago))

    def test_old_runs_go_but_each_tasks_newest_stays(self):
        gone = [self.run_at(FIVE, 40), self.run_at(FIVE, 35)]
        kept = self.run_at(FIVE, 1)
        monthly = self.run_at(DAILY, 45)  # its only run: its heartbeat
        self.assertEqual(heartbeats.prune(), 2)
        self.assertEqual(set(TaskRun.objects.values_list("pk", flat=True)),
                         {kept.pk, monthly.pk})
        self.assertFalse(TaskRun.objects.filter(pk__in=[r.pk for r in gone]).exists())

    @override_settings(MONIT_RUN_RETENTION_DAYS=3)
    def test_the_retention_is_a_setting(self):
        self.run_at(FIVE, 4)
        kept = self.run_at(FIVE, 2)
        self.run_at(FIVE, 0)
        heartbeats.prune()
        self.assertTrue(TaskRun.objects.filter(pk=kept.pk).exists())
        self.assertEqual(TaskRun.objects.count(), 2)

    def test_monit_prune_prunes_runs_snapshots_and_unscheduled_entries(self):
        self.run_at(FIVE, 40)
        self.run_at(FIVE, 0)
        old = Snapshot.objects.create()
        Snapshot.objects.filter(pk=old.pk).update(
            created=timezone.now() - timedelta(hours=72))
        long_ago = timezone.now() - timedelta(days=40)
        BeatEntry.objects.create(name="retired", task="t.retired",
                                 first_seen=long_ago, last_seen=long_ago)
        BeatEntry.objects.create(name="daily", task=DAILY,
                                 first_seen=long_ago, last_seen=long_ago)
        self.assertEqual(tasks.monit_prune(), {"snapshots": 1, "runs": 1})
        self.assertEqual(list(BeatEntry.objects.values_list("name", flat=True)),
                         ["daily"])


class JobSourceTests(TestCase):
    def test_run_records_are_a_source_in_the_canonical_words(self):
        now = timezone.now()
        for minutes, status in ((4, "success"), (3, "failed"), (2, "retry"),
                                (1, "running")):
            TaskRun.objects.create(task=LEVY, task_id=f"r{minutes}", status=status,
                                   started_at=now - timedelta(minutes=minutes),
                                   summary="paid=3" if status == "success" else "",
                                   error="ValueError: x" if status == "failed" else "")
        rows = jobs.recent_jobs(source_key="beat")
        self.assertEqual([(r.raw_status, r.status) for r in rows],
                         [("running", jobs.RUNNING), ("retry", jobs.PENDING),
                          ("failed", jobs.FAILED), ("success", jobs.DONE)])
        done = rows[-1]
        self.assertEqual(done.subject, f"{LEVY} — paid=3")
        self.assertEqual(done.task_id, "r4")
        self.assertEqual(rows[2].error, "ValueError: x")
        self.assertEqual(done.source_label, "Scheduled tasks")

    def test_faucet_runs_are_a_source_with_a_status_read_off_their_counts(self):
        if not django_apps.is_installed("toto.assets"):
            self.skipTest("toto.assets is not installed on this host")
        from toto.assets.models import FaucetRun

        finished = timezone.now()
        FaucetRun.objects.create(period_label="h1")
        FaucetRun.objects.create(period_label="h2", paid=3, finished_at=finished)
        FaucetRun.objects.create(period_label="h3", failed=2, finished_at=finished,
                                 detail="reserve is dry")
        FaucetRun.objects.create(period_label="h4", paid=1, failed=1,
                                 finished_at=finished)
        rows = {r.subject.split(":")[0]: r for r in jobs.recent_jobs(source_key="faucet")}
        self.assertEqual({label: (r.raw_status, r.status) for label, r in rows.items()},
                         {"h1": ("running", jobs.RUNNING), "h2": ("done", jobs.DONE),
                          "h3": ("failed", jobs.FAILED), "h4": ("partial", jobs.OTHER)})
        self.assertEqual(rows["h3"].error, "reserve is dry")
        self.assertIn("faucet", {s.key for s in jobs.available_sources()})
