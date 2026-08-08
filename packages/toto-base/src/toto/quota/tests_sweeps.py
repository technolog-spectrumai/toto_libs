"""The stuck-run sweeper's registry and service, against a synthetic policy.

Runs under toto.tax.testing.settings (quota + tax installed): the swept model
is tax.TaxArrearsCase, chosen because it is installed here and has a status
field, an opened_at timestamp, and a spare CharField to stand in for a task
id. The real per-app policies are exercised in their apps' own suites.
"""

import datetime
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from toto.quota import sweeps
from toto.quota.sweeps import DuplicateSweepPolicy, StuckRunPolicy

User = get_user_model()

_CLOSED = []


def _probe_close(row, reason):
    _CLOSED.append((row.pk, reason))
    row.status = "resolved"
    row.save(update_fields=["status"])


def _exploding_close(row, reason):
    raise RuntimeError("closer blew up")


def make_policy(**overrides):
    defaults = dict(
        model_label="tax.TaxArrearsCase",
        active_values=("open",),
        closer="toto.quota.tests_sweeps._probe_close",
        cutoff_seconds=3600,
        reference_fields=("warned_at", "opened_at"),
        task_id_field="",
    )
    defaults.update(overrides)
    return StuckRunPolicy(**defaults)


class RegistryTests(TestCase):
    def setUp(self):
        self._saved = sweeps.all_policies()
        sweeps.clear_registry()
        self.addCleanup(self._restore)

    def _restore(self):
        sweeps.clear_registry()
        for policy in self._saved:
            sweeps.register(policy)

    def test_register_preserves_order_and_rejects_conflicts(self):
        a = make_policy(model_label="a.Model")
        b = make_policy(model_label="b.Model")
        sweeps.register(a)
        sweeps.register(b)
        sweeps.register(a)  # identical redeclaration is fine
        self.assertEqual([p.model_label for p in sweeps.all_policies()],
                         ["a.Model", "b.Model"])
        with self.assertRaises(DuplicateSweepPolicy):
            sweeps.register(make_policy(model_label="a.Model", cutoff_seconds=1))


class RunSweepsTests(TestCase):
    def setUp(self):
        self._saved = sweeps.all_policies()
        sweeps.clear_registry()
        self.addCleanup(self._restore)
        _CLOSED.clear()
        self.user = User.objects.create_user("alice", password="pw")

    def _restore(self):
        sweeps.clear_registry()
        for policy in self._saved:
            sweeps.register(policy)

    _rule_counter = 0

    def _case(self, *, opened_hours_ago, status="open", warned_at=None,
              reason_field=""):
        from toto.tax.models import TaxArrearsCase, TaxRule

        # One rule per case: at most one live case may exist per (user, rule),
        # and these tests want several open rows side by side.
        type(self)._rule_counter += 1
        rule = TaxRule.objects.create(
            metric_code=f"sweeptest.metric{self._rule_counter}")
        case = TaxArrearsCase.objects.create(
            user=self.user, rule=rule, status=status,
            opened_at=timezone.now() - datetime.timedelta(hours=opened_hours_ago),
            warned_at=warned_at, resolution_reason=reason_field,
        )
        return case

    def test_closes_old_rows_and_leaves_fresh_and_terminal_ones(self):
        old = self._case(opened_hours_ago=2)
        fresh = self._case(opened_hours_ago=0)
        done = self._case(opened_hours_ago=5, status="resolved")
        sweeps.register(make_policy())

        closed = sweeps.run_sweeps()

        self.assertEqual(closed, {"tax.TaxArrearsCase": 1})
        self.assertEqual([pk for pk, _r in _CLOSED], [old.pk])
        self.assertIn("stuck-run sweeper", _CLOSED[0][1])
        fresh.refresh_from_db()
        self.assertEqual(fresh.status, "open")
        done.refresh_from_db()
        self.assertEqual(done.status, "resolved")

    def test_reference_field_fallback_prefers_the_first_non_null(self):
        # warned_at recent but opened_at old: the FIRST field wins, so the
        # row is fresh by warned_at and stays open.
        self._case(opened_hours_ago=10, warned_at=timezone.now())
        sweeps.register(make_policy())

        closed = sweeps.run_sweeps()

        self.assertEqual(closed["tax.TaxArrearsCase"], 0)

    def test_missing_model_and_broken_closer_are_skipped(self):
        self._case(opened_hours_ago=2)
        sweeps.register(make_policy(model_label="nosuch.Model"))
        sweeps.register(make_policy(closer="toto.quota.tests_sweeps._no_such_fn",
                                    cutoff_seconds=1800))

        closed = sweeps.run_sweeps()

        self.assertEqual(closed, {})  # both skipped, neither raised

    def test_exploding_closer_does_not_stop_the_sweep(self):
        self._case(opened_hours_ago=2)
        second = self._case(opened_hours_ago=3)
        # Two policies over the same statuses: first one explodes per row,
        # second closes. The registry forbids duplicate labels, so vary a
        # copy via cutoff on... same label is forbidden — use one policy with
        # the exploding closer and assert zero closes but no exception.
        sweeps.clear_registry()
        sweeps.register(make_policy(closer="toto.quota.tests_sweeps._exploding_close"))

        closed = sweeps.run_sweeps()

        self.assertEqual(closed["tax.TaxArrearsCase"], 0)
        second.refresh_from_db()
        self.assertEqual(second.status, "open")

    def test_revokes_before_closing_when_a_task_id_exists(self):
        self._case(opened_hours_ago=2, reason_field="faketask-123")
        sweeps.register(make_policy(task_id_field="resolution_reason"))
        control = MagicMock()

        with patch("celery.current_app") as capp:
            capp.control = control
            sweeps.run_sweeps()

        control.revoke.assert_called_once_with(
            "faketask-123", terminate=True, signal="SIGTERM")
        self.assertEqual(len(_CLOSED), 1)
