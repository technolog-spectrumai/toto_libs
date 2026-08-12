"""The arrears lifecycle: open → warn → resolve, or a week → frozen."""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.db import IntegrityError, transaction
from toto.assets.testing import LedgerTestCase as TestCase
from django.utils import timezone

from toto.events.models import EventInvite, ScheduledEvent

from .. import arrears
from ..models import ArrearsStatus, TaxArrearsCase
from .factories import GB, make_person, make_rule, make_user


def open_case(user, rule, **kwargs):
    defaults = {"stored_raw": 3 * GB, "shortfall": Decimal("1"), "asset": "ASR"}
    defaults.update(kwargs)
    return arrears.open_or_touch_case(user, rule, **defaults)


class WarningTests(TestCase):
    def setUp(self):
        self.rule = make_rule()
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
        # The notice used to promise permanent deletion of randomly chosen
        # files. It now promises the opposite, and says so in the provider's
        # own words — asserted here because it is the whole change.
        self.assertIn("nothing you have stored is deleted", event.description)
        self.assertNotIn("deleted to", event.description)
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


class FreezeTests(TestCase):
    """Past the deadline, new usage stops. Nothing is ever taken away."""

    def setUp(self):
        self.rule = make_rule()
        self.user = make_user("alice")
        self.person = make_person(self.user)

    def _warned_case(self, days_past_deadline=0):
        case = open_case(self.user, self.rule)
        if days_past_deadline:
            case.deadline_at = timezone.now() - datetime.timedelta(days=days_past_deadline)
            case.save(update_fields=["deadline_at"])
        return case

    def test_not_frozen_before_the_deadline(self):
        self._warned_case()
        self.assertFalse(arrears.is_frozen(self.user))

    def test_frozen_past_the_deadline(self):
        self._warned_case(days_past_deadline=1)
        self.assertTrue(arrears.is_frozen(self.user))

    def test_paying_thaws(self):
        self._warned_case(days_past_deadline=1)
        arrears.resolve_case(self.user, self.rule, reason=arrears.REASON_PAID)
        self.assertFalse(arrears.is_frozen(self.user))

    def test_shedding_everything_thaws(self):
        self._warned_case(days_past_deadline=1)
        arrears.resolve_case(self.user, self.rule,
                             reason=arrears.REASON_NOTHING_HELD)
        self.assertFalse(arrears.is_frozen(self.user))

    def test_a_stranger_is_never_frozen(self):
        self._warned_case(days_past_deadline=1)
        self.assertFalse(arrears.is_frozen(make_user("bob")))

    def test_an_anonymous_visitor_is_never_frozen(self):
        from django.contrib.auth.models import AnonymousUser

        self._warned_case(days_past_deadline=1)
        self.assertFalse(arrears.is_frozen(AnonymousUser()))
        self.assertFalse(arrears.is_frozen(None))

    def test_reoffense_after_resolution_gets_a_fresh_case_and_week(self):
        first = open_case(self.user, self.rule)
        arrears.resolve_case(self.user, self.rule, reason="paid")

        second = open_case(self.user, self.rule)

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(second.failed_days, 1)
        self.assertEqual(second.status, ArrearsStatus.WARNED)


class FreezeStopsNewUsageTests(TestCase):
    """End to end: the debt stops NEW work and touches nothing that exists."""

    def setUp(self):
        self.rule = make_rule()
        self.user = make_user("alice")
        make_person(self.user)

    def test_a_frozen_user_cannot_add_but_keeps_everything(self):
        from toto.quota.api import InArrears, check_quota
        from toto.vault.models import VaultFile, VaultQuotaPolicy

        from .factories import make_vault_file

        make_vault_file(self.user, 3 * GB)
        make_vault_file(self.user, 2 * GB)
        before = set(VaultFile.objects.filter(owner=self.user)
                     .values_list("pk", flat=True))

        # Before the deadline: warned, but still working.
        case = open_case(self.user, self.rule)
        check_quota(VaultQuotaPolicy, "storage.request", 1, self.user)

        # Past it: new work refuses…
        case.deadline_at = timezone.now() - datetime.timedelta(days=1)
        case.save(update_fields=["deadline_at"])
        with self.assertRaises(InArrears) as caught:
            check_quota(VaultQuotaPolicy, "storage.request", 1, self.user)
        self.assertEqual(caught.exception.status_code, 402)

        # …and EVERY file is still there. This is the whole change: the old
        # code deleted files at random one week after a missed payment.
        after = set(VaultFile.objects.filter(owner=self.user)
                    .values_list("pk", flat=True))
        self.assertEqual(after, before)

    def test_clearing_the_debt_restores_writes(self):
        from toto.quota.api import InArrears, check_quota
        from toto.vault.models import VaultQuotaPolicy

        case = open_case(self.user, self.rule)
        case.deadline_at = timezone.now() - datetime.timedelta(days=1)
        case.save(update_fields=["deadline_at"])
        with self.assertRaises(InArrears):
            check_quota(VaultQuotaPolicy, "storage.request", 1, self.user)

        arrears.resolve_case(self.user, self.rule, reason=arrears.REASON_PAID)

        check_quota(VaultQuotaPolicy, "storage.request", 1, self.user)

    def test_one_debtor_does_not_freeze_the_platform(self):
        from toto.quota.api import check_quota
        from toto.vault.models import VaultQuotaPolicy

        case = open_case(self.user, self.rule)
        case.deadline_at = timezone.now() - datetime.timedelta(days=1)
        case.save(update_fields=["deadline_at"])

        check_quota(VaultQuotaPolicy, "storage.request", 1, make_user("bob"))
