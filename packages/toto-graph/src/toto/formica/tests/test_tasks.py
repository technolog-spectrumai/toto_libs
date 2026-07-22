from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from toto.formica import tasks
from toto.formica.models import Colony, ColonyCycle

from .helpers import make_bento_templates, make_colony


class BeatScanTests(TestCase):
    def setUp(self):
        make_bento_templates()

    def _due_colony(self, **overrides):
        colony = make_colony(**overrides)
        Colony.objects.filter(pk=colony.pk).update(
            last_run_at=timezone.now() - timedelta(hours=2)
        )
        colony.refresh_from_db()
        return colony

    def test_due_colony_dispatched_once_and_claimed(self):
        colony = self._due_colony()
        with patch.object(tasks.run_colony_cycle, "delay") as delay:
            result = tasks.formica_beat_scan.apply(args=()).result
        self.assertEqual(result["dispatched"], 1)
        delay.assert_called_once()
        cycle = colony.cycles.get()
        self.assertEqual(cycle.number, 1)
        self.assertEqual(cycle.triggered_by, ColonyCycle.TRIGGERED_BEAT)
        self.assertGreater(cycle.rng_seed, 0)
        colony.refresh_from_db()
        self.assertGreater(colony.last_run_at, timezone.now() - timedelta(minutes=1))

        # tick consumed: an immediate second scan does nothing
        with patch.object(tasks.run_colony_cycle, "delay") as delay:
            result = tasks.formica_beat_scan.apply(args=()).result
        self.assertEqual(result["dispatched"], 0)

    def test_never_run_colony_is_due(self):
        make_colony()  # last_run_at None
        with patch.object(tasks.run_colony_cycle, "delay") as delay:
            result = tasks.formica_beat_scan.apply(args=()).result
        self.assertEqual(result["dispatched"], 1)
        delay.assert_called_once()

    def test_paused_and_disabled_colonies_skipped(self):
        self._due_colony(name="Paused", paused=True)
        self._due_colony(name="Disabled", enabled=False)
        with patch.object(tasks.run_colony_cycle, "delay") as delay:
            result = tasks.formica_beat_scan.apply(args=()).result
        self.assertEqual(result["dispatched"], 0)
        delay.assert_not_called()

    def test_in_flight_cycle_blocks_dispatch(self):
        colony = self._due_colony()
        ColonyCycle.objects.create(
            colony=colony, number=1, status=ColonyCycle.STATUS_RUNNING
        )
        with patch.object(tasks.run_colony_cycle, "delay") as delay:
            result = tasks.formica_beat_scan.apply(args=()).result
        self.assertEqual(result["dispatched"], 0)
        self.assertEqual(result["skipped"], 1)
        delay.assert_not_called()

    def test_cycle_numbers_are_sequential(self):
        colony = self._due_colony()
        ColonyCycle.objects.create(
            colony=colony, number=7, status=ColonyCycle.STATUS_COMPLETED
        )
        with patch.object(tasks.run_colony_cycle, "delay"):
            tasks.formica_beat_scan.apply(args=())
        self.assertEqual(colony.cycles.order_by("-number").first().number, 8)


class TriggerCycleTests(TestCase):
    def setUp(self):
        make_bento_templates()
        self.colony = make_colony()

    def test_manual_trigger_queues_when_celery_up(self):
        with patch("toto.celery_utils.celery_available", return_value=True), \
             patch.object(tasks.run_colony_cycle, "delay") as delay:
            cycle, queued = tasks.trigger_cycle(self.colony)
        self.assertTrue(queued)
        delay.assert_called_once_with(cycle.pk)
        self.assertEqual(cycle.triggered_by, ColonyCycle.TRIGGERED_MANUAL)

    def test_manual_trigger_runs_inline_without_celery(self):
        with patch("toto.celery_utils.celery_available", return_value=False), \
             patch.object(tasks, "run_colony_cycle_inline") as inline:
            cycle, queued = tasks.trigger_cycle(self.colony)
        self.assertFalse(queued)
        inline.assert_called_once_with(cycle.pk)

    def test_second_trigger_while_in_flight_raises(self):
        ColonyCycle.objects.create(
            colony=self.colony, number=1, status=ColonyCycle.STATUS_PENDING
        )
        with self.assertRaises(tasks.CycleInProgress):
            tasks.trigger_cycle(self.colony)

    def test_run_task_delegates_to_engine(self):
        cycle = ColonyCycle.objects.create(colony=self.colony, number=1)
        with patch("toto.formica.colony.cycle.execute_cycle") as execute:
            tasks.run_colony_cycle.apply(args=(cycle.pk,))
        execute.assert_called_once_with(cycle.pk)

    def test_apply_proposal_task_delegates(self):
        with patch("toto.formica.services.apply.run") as apply_run:
            tasks.apply_formica_proposal.apply(args=(123,))
        apply_run.assert_called_once_with(123, user_id=None)


class RetentionTests(TestCase):
    def setUp(self):
        make_bento_templates()
        self.colony = make_colony()

    def test_old_reports_pruned_but_proposal_cycles_kept(self):
        from toto.formica.models import ForagerReport, FormicaProposal

        cycles = [
            ColonyCycle.objects.create(
                colony=self.colony, number=i + 1,
                status=ColonyCycle.STATUS_COMPLETED,
            )
            for i in range(6)
        ]
        for cycle in cycles:
            ForagerReport.objects.create(cycle=cycle, payload={"n": cycle.number})
        FormicaProposal.objects.create(colony=self.colony, cycle=cycles[0])

        with self.settings(FORMICA_REPORT_RETENTION=2, FORMICA_CYCLE_RETENTION=3):
            result = tasks.prune_retention()

        # newest 2 reports kept, 4 deleted
        self.assertEqual(result["reports_pruned"], 4)
        kept = set(ForagerReport.objects.values_list("cycle__number", flat=True))
        self.assertEqual(kept, {5, 6})
        # cycles beyond the newest 3 go — except #1, which owns a proposal
        remaining = set(self.colony.cycles.values_list("number", flat=True))
        self.assertEqual(remaining, {1, 4, 5, 6})

    def test_running_cycles_never_pruned(self):
        for i in range(4):
            ColonyCycle.objects.create(
                colony=self.colony, number=i + 1,
                status=ColonyCycle.STATUS_RUNNING if i == 0
                else ColonyCycle.STATUS_COMPLETED,
            )
        with self.settings(FORMICA_CYCLE_RETENTION=1, FORMICA_REPORT_RETENTION=1):
            tasks.prune_retention()
        self.assertTrue(
            self.colony.cycles.filter(status=ColonyCycle.STATUS_RUNNING).exists()
        )
