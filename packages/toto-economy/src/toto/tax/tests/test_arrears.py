"""The arrears lifecycle: open → warn → resolve / week → enforce."""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.db import IntegrityError, transaction
from toto.assets.testing import LedgerTestCase as TestCase
from django.utils import timezone

from toto.events.models import EventInvite, ScheduledEvent

from .. import arrears
from ..models import (
    ArrearsStatus, EnforcementAction, TaxArrearsCase, TaxEnforcementAction,
)
from .factories import GB, FakeProvider, make_person, make_rule, make_user


def open_case(user, rule, **kwargs):
    defaults = {"stored_raw": 3 * GB, "allowance_raw": GB,
                "shortfall": Decimal("1"), "asset": "ASR"}
    defaults.update(kwargs)
    return arrears.open_or_touch_case(user, rule, **defaults)


class WarningTests(TestCase):
    def setUp(self):
        self.rule = make_rule(allowance="1")
        self.user = make_user("alice")
        self.person = make_person(self.user)

    def test_first_failure_opens_case_creates_event_and_invite(self):
        case = open_case(self.user, self.rule)

        self.assertEqual(case.status, ArrearsStatus.WARNED)
        self.assertEqual(case.failed_days, 1)
        self.assertEqual(case.warning_channel, "event")
        self.assertEqual(case.deadline_at, case.warned_at + datetime.timedelta(days=7))

        event = ScheduledEvent.objects.get(pk=case.warning_event_uid)
        self.assertEqual(event.owner, self.person)
        self.assertEqual(event.category.name, "Platform notices")
        self.assertNotIn("alice", event.title)  # neutral title, details inside
        self.assertIn("permanently deleted", event.description)
        self.assertTrue(EventInvite.objects.filter(event=event, person=self.person).exists())

    def test_rerun_same_day_is_idempotent(self):
        first = open_case(self.user, self.rule)
        second = open_case(self.user, self.rule)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(second.failed_days, 1)
        self.assertEqual(ScheduledEvent.objects.count(), 1)

    def test_failure_next_day_touches_without_a_second_event(self):
        case = open_case(self.user, self.rule)
        case.last_failure_at = timezone.now() - datetime.timedelta(days=1)
        case.save(update_fields=["last_failure_at"])

        case = open_case(self.user, self.rule, shortfall=Decimal("2"))

        self.assertEqual(case.failed_days, 2)
        self.assertEqual(case.last_shortfall_display, Decimal("2"))
        self.assertEqual(ScheduledEvent.objects.count(), 1)

    def test_resolve_closes_case_and_removes_the_event(self):
        case = open_case(self.user, self.rule)

        resolved = arrears.resolve_case(self.user, self.rule, reason=arrears.REASON_PAID)

        self.assertEqual(resolved.pk, case.pk)
        self.assertEqual(resolved.status, ArrearsStatus.RESOLVED)
        self.assertEqual(resolved.resolution_reason, "paid")
        self.assertFalse(ScheduledEvent.objects.filter(pk=case.warning_event_uid).exists())

    def test_resolve_without_active_case_is_a_noop(self):
        self.assertIsNone(arrears.resolve_case(self.user, self.rule, reason="paid"))

    def test_no_person_warns_without_event_and_the_clock_runs(self):
        loner = make_user("loner")

        case = open_case(loner, self.rule)

        self.assertEqual(case.status, ArrearsStatus.WARNED)
        self.assertEqual(case.warning_channel, "none")
        self.assertIsNone(case.warning_event_uid)
        self.assertIsNotNone(case.deadline_at)
        self.assertEqual(ScheduledEvent.objects.count(), 0)

    def test_event_error_keeps_case_open_and_the_next_day_retries(self):
        with patch.object(arrears.notices, "create_warning_event",
                          side_effect=RuntimeError("events down")):
            case = open_case(self.user, self.rule)

        self.assertEqual(case.status, ArrearsStatus.OPEN)
        self.assertIsNone(case.warned_at)
        self.assertEqual(case.failed_days, 1)

        # The next attempt (events back up) delivers, and the week starts NOW.
        case = open_case(self.user, self.rule)
        self.assertEqual(case.status, ArrearsStatus.WARNED)
        self.assertIsNotNone(case.warned_at)

    def test_second_active_case_is_impossible(self):
        open_case(self.user, self.rule)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TaxArrearsCase.objects.create(user=self.user, rule=self.rule)


class EnforcementTests(TestCase):
    def setUp(self):
        self.rule = make_rule(allowance="1")
        self.user = make_user("alice")
        self.person = make_person(self.user)
        self.provider = FakeProvider({self.user.pk: 5 * GB})

    def _warned_case(self, days_past_deadline=0):
        case = open_case(self.user, self.rule)
        if days_past_deadline:
            case.deadline_at = timezone.now() - datetime.timedelta(days=days_past_deadline)
            case.save(update_fields=["deadline_at"])
        return case

    def test_no_enforcement_before_the_deadline(self):
        case = self._warned_case()

        self.assertIsNone(arrears.enforce_if_due(case, self.provider))
        self.assertEqual(self.provider.enforce_calls, [])

    def test_enforces_to_the_current_allowance_past_deadline(self):
        case = self._warned_case(days_past_deadline=1)

        result = arrears.enforce_if_due(case, self.provider)

        self.assertEqual(self.provider.enforce_calls, [(self.user.pk, GB)])
        self.assertTrue(result.reached_target)
        case.refresh_from_db()
        self.assertEqual(case.status, ArrearsStatus.ENFORCED)
        self.assertTrue(case.reached_target)
        self.assertEqual(case.enforcement_summary["deleted_count"], 4)
        self.assertEqual(case.enforcement_summary["final_raw"], GB)
        self.assertEqual(
            TaxEnforcementAction.objects.filter(
                case=case, action=EnforcementAction.DELETED).count(),
            4,
        )
        # Warning gone; the after-the-fact notice is on the calendar instead.
        self.assertFalse(ScheduledEvent.objects.filter(pk=case.warning_event_uid).exists())
        notice = ScheduledEvent.objects.get()
        self.assertIn("storage reduced", notice.title.lower())

    def test_partial_enforcement_is_terminal_and_flagged(self):
        self.provider.protect_all = True
        case = self._warned_case(days_past_deadline=1)

        result = arrears.enforce_if_due(case, self.provider)

        self.assertFalse(result.reached_target)
        case.refresh_from_db()
        self.assertEqual(case.status, ArrearsStatus.ENFORCED)
        self.assertFalse(case.reached_target)
        self.assertEqual(
            TaxEnforcementAction.objects.filter(
                case=case, action=EnforcementAction.SKIPPED_PROTECTED).count(),
            1,
        )

    def test_reoffense_after_resolution_gets_a_fresh_case_and_week(self):
        first = open_case(self.user, self.rule)
        arrears.resolve_case(self.user, self.rule, reason="paid")

        second = open_case(self.user, self.rule)

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(second.failed_days, 1)
        self.assertEqual(second.status, ArrearsStatus.WARNED)

    def test_reoffense_after_enforcement_gets_a_fresh_case(self):
        case = self._warned_case(days_past_deadline=1)
        arrears.enforce_if_due(case, self.provider)

        fresh = open_case(self.user, self.rule)

        self.assertNotEqual(fresh.pk, case.pk)
        self.assertEqual(fresh.status, ArrearsStatus.WARNED)
