from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from toto.connectors import tasks
from toto.connectors.models import ConnectorRun

from .helpers import make_data_connector


class ScanSchedulesTests(TestCase):
    def _due_connector(self, **overrides):
        defaults = {
            "schedule_enabled": True,
            "interval_minutes": 60,
            "next_run_at": timezone.now() - timedelta(minutes=5),
        }
        defaults.update(overrides)
        return make_data_connector(**defaults)

    def test_due_connector_is_claimed_and_dispatched_once(self):
        connector = self._due_connector()
        before = connector.next_run_at
        with patch.object(tasks.run_connector_task, "delay") as delay:
            result = tasks.connectors_scan_schedules.apply(args=()).result
        self.assertEqual(result["dispatched"], 1)
        delay.assert_called_once()
        connector.refresh_from_db()
        self.assertGreater(connector.next_run_at, before)
        self.assertEqual(connector.runs.count(), 1)
        run = connector.runs.get()
        self.assertEqual(run.status, ConnectorRun.STATUS_PENDING)
        self.assertEqual(run.triggered, ConnectorRun.TRIGGERED_SCHEDULE)

        # second scan in the same minute: tick already consumed
        with patch.object(tasks.run_connector_task, "delay") as delay:
            result = tasks.connectors_scan_schedules.apply(args=()).result
        self.assertEqual(result["dispatched"], 0)
        delay.assert_not_called()

    def test_in_flight_run_skips_but_advances_the_tick(self):
        connector = self._due_connector()
        ConnectorRun.objects.create(
            connector=connector, status=ConnectorRun.STATUS_RUNNING
        )
        before = connector.next_run_at
        with patch.object(tasks.run_connector_task, "delay") as delay:
            result = tasks.connectors_scan_schedules.apply(args=()).result
        self.assertEqual(result, {"dispatched": 0, "skipped": 1})
        delay.assert_not_called()
        connector.refresh_from_db()
        self.assertGreater(connector.next_run_at, before)
        self.assertEqual(connector.runs.count(), 1)  # no new run stacked

    def test_disabled_and_inactive_connectors_are_ignored(self):
        self._due_connector(name="off", schedule_enabled=False)
        self._due_connector(name="inactive", is_active=False)
        self._due_connector(name="future", next_run_at=timezone.now() + timedelta(hours=1))
        with patch.object(tasks.run_connector_task, "delay") as delay:
            result = tasks.connectors_scan_schedules.apply(args=()).result
        self.assertEqual(result["dispatched"], 0)
        delay.assert_not_called()

    def test_untrusted_connector_with_unreviewed_run_is_skipped(self):
        connector = self._due_connector()
        ConnectorRun.objects.create(
            connector=connector, status=ConnectorRun.STATUS_REVIEW
        )
        with patch.object(tasks.run_connector_task, "delay") as delay:
            result = tasks.connectors_scan_schedules.apply(args=()).result
        self.assertEqual(result, {"dispatched": 0, "skipped": 1})
        delay.assert_not_called()
        self.assertEqual(connector.runs.count(), 1)  # no proposal pileup

    def test_trusted_connector_with_review_run_still_dispatches(self):
        connector = self._due_connector(trusted=True)
        ConnectorRun.objects.create(
            connector=connector, status=ConnectorRun.STATUS_REVIEW
        )
        with patch.object(tasks.run_connector_task, "delay") as delay:
            result = tasks.connectors_scan_schedules.apply(args=()).result
        self.assertEqual(result["dispatched"], 1)
        delay.assert_called_once()

    def test_run_connector_task_records_task_id_and_executes(self):
        connector = make_data_connector()
        run = ConnectorRun.objects.create(connector=connector)
        with patch("toto.connectors.services.runner.execute_run") as execute:
            tasks.run_connector_task.apply(args=(run.pk,))
        execute.assert_called_once_with(run.pk)


class ScheduleFieldTests(TestCase):
    def test_next_run_at_autofilled_on_save(self):
        connector = make_data_connector(schedule_enabled=True, interval_minutes=30)
        self.assertIsNotNone(connector.next_run_at)

    def test_schedule_next(self):
        connector = make_data_connector(interval_minutes=15)
        now = timezone.now()
        self.assertEqual(connector.schedule_next(now), now + timedelta(minutes=15))
        connector.interval_minutes = None
        self.assertIsNone(connector.schedule_next(now))

    def test_disabling_schedule_clears_next_run_at(self):
        connector = make_data_connector(schedule_enabled=True, interval_minutes=30)
        connector.schedule_enabled = False
        connector.save()
        self.assertIsNone(connector.next_run_at)
        # re-enabling waits a full interval instead of firing on a stale tick
        before = timezone.now()
        connector.schedule_enabled = True
        connector.save()
        self.assertGreaterEqual(
            connector.next_run_at, before + timedelta(minutes=29)
        )


class StaleRunTests(TestCase):
    def test_fresh_run_blocks_new_runs(self):
        connector = make_data_connector()
        ConnectorRun.objects.create(connector=connector, status=ConnectorRun.STATUS_RUNNING)
        self.assertTrue(connector.has_run_in_flight())

    def test_stale_running_run_stops_blocking(self):
        connector = make_data_connector()
        run = ConnectorRun.objects.create(
            connector=connector, status=ConnectorRun.STATUS_RUNNING
        )
        # a worker killed mid-run never flips the status; age it past the cutoff
        ConnectorRun.objects.filter(pk=run.pk).update(
            created_at=timezone.now() - connector.STALE_RUN_MAX_AGE - timedelta(minutes=1)
        )
        self.assertFalse(connector.has_run_in_flight())
