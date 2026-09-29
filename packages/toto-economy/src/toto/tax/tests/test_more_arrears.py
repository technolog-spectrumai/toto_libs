"""Arrears around the edges: the grace setting, the warning's wording when it
has little to go on, and the failures that must never refuse service."""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.db import DatabaseError
from django.test import override_settings
from django.utils import timezone

from toto.assets.testing import LedgerTestCase as TestCase

from .. import arrears, notices
from ..models import ArrearsStatus, TaxArrearsCase, TaxRule, TimeGrant
from .factories import GB, make_person, make_rule, make_user


class GraceTests(TestCase):
    def setUp(self):
        self.rule = make_rule()
        self.user = make_user("alice")
        make_person(self.user)

    @override_settings(TAX_ARREARS_GRACE_DAYS=3)
    def test_the_deadline_follows_the_grace_setting(self):
        before = timezone.now()
        case = arrears.open_or_touch_case(self.user, self.rule, stored_raw=GB)
        self.assertEqual(case.status, ArrearsStatus.WARNED)
        self.assertGreaterEqual(case.deadline_at, before + timedelta(days=3))
        self.assertLess(case.deadline_at, before + timedelta(days=3, minutes=1))

    def test_the_default_grace_is_a_week(self):
        self.assertEqual(arrears.grace_days(), 7)

    def test_the_failed_day_snapshot_is_kept_for_the_warning(self):
        case = arrears.open_or_touch_case(
            self.user, self.rule, stored_raw=5 * GB,
            shortfall=Decimal("1.25"), asset="ASR")
        case.refresh_from_db()
        self.assertEqual(case.failed_days, 1)
        self.assertEqual(case.last_stored_raw, 5 * GB)
        self.assertEqual(case.last_shortfall_display, Decimal("1.25"))
        self.assertEqual(case.last_shortfall_asset, "ASR")
        self.assertEqual(case.warning_channel, "event")


class NeverRefuseServiceTests(TestCase):
    def setUp(self):
        self.rule = make_rule()
        self.user = make_user("alice")

    def test_an_unreadable_arrears_table_answers_not_frozen(self):
        TaxArrearsCase.objects.create(
            user=self.user, rule=self.rule, status=ArrearsStatus.WARNED,
            deadline_at=timezone.now() - timedelta(days=1))
        self.assertTrue(arrears.is_frozen(self.user))
        with mock.patch.object(TaxArrearsCase.objects, "filter",
                               side_effect=DatabaseError("locked")):
            self.assertFalse(arrears.is_frozen(self.user))

    def test_an_open_case_without_a_deadline_does_not_freeze(self):
        TaxArrearsCase.objects.create(user=self.user, rule=self.rule,
                                      status=ArrearsStatus.OPEN)
        self.assertFalse(arrears.is_frozen(self.user))

    def test_a_warning_event_that_cannot_be_removed_does_not_block_resolution(self):
        make_person(self.user)
        case = arrears.open_or_touch_case(self.user, self.rule, stored_raw=GB)
        self.assertIsNotNone(case.warning_event_uid)
        from toto.events.models import ScheduledEvent

        with mock.patch.object(ScheduledEvent.objects, "filter",
                               side_effect=DatabaseError("gone")):
            resolved = arrears.resolve_case(self.user, self.rule,
                                            reason=arrears.REASON_PAID)
        self.assertEqual(resolved.status, ArrearsStatus.RESOLVED)
        self.assertEqual(resolved.resolution_reason, "paid")


class WarningWordingTests(TestCase):
    def setUp(self):
        self.user = make_user("alice")
        self.person = make_person(self.user)
        self.deadline = timezone.now() + timedelta(days=7)

    def test_without_numbers_it_speaks_of_the_amount_due(self):
        event = notices.create_warning_event(self.user, deadline=self.deadline)
        self.assertIn("short by the amount due", event.description)
        self.assertIn("you will not be able to add anything new",
                      event.description)

    def test_the_numbers_and_the_consequence_go_in_the_description(self):
        event = notices.create_warning_event(
            self.user, deadline=self.deadline, shortfall=Decimal("0.5"),
            asset="ASR", consequence="no new dials")
        self.assertIn("short by 0.5 ASR", event.description)
        self.assertIn("then, no new dials.", event.description)

    def test_the_title_is_neutral_because_the_calendar_is_shared(self):
        event = notices.create_warning_event(
            self.user, deadline=self.deadline, shortfall=Decimal("3"), asset="ASR")
        self.assertNotIn("3", event.title.replace(
            f"{self.deadline:%Y-%m-%d}", ""))
        self.assertNotIn("ASR", event.title)
        self.assertFalse(event.public)
        self.assertEqual(event.start_time, self.deadline)

    def test_the_invite_promises_nothing_is_deleted(self):
        from toto.events.models import EventInvite

        event = notices.create_warning_event(self.user, deadline=self.deadline)
        invite = EventInvite.objects.get(event=event)
        self.assertEqual(invite.person, self.person)
        self.assertIn("nothing you have is deleted", invite.note)

    def test_all_notices_share_one_category(self):
        first = notices.create_warning_event(self.user, deadline=self.deadline)
        second = notices.create_warning_event(self.user, deadline=self.deadline)
        self.assertEqual(first.category_id, second.category_id)
        self.assertEqual(first.category.name, notices.CATEGORY_NAME)

    def test_a_rule_whose_provider_left_warns_with_the_default_consequence(self):
        rule = make_rule(metric_code="nothing.registered")
        case = arrears.open_or_touch_case(self.user, rule, stored_raw=1)
        from toto.events.models import ScheduledEvent

        event = ScheduledEvent.objects.get(pk=case.warning_event_uid)
        self.assertIn("you will not be able to add anything new",
                      event.description)


class ModelTextTests(TestCase):
    def test_a_rule_says_whether_it_is_armed(self):
        self.assertEqual(str(make_rule()), "storage.gb_day (armed)")
        self.assertEqual(str(TaxRule(metric_code="x.y", active=False)),
                         "x.y (unarmed)")

    def test_a_case_is_active_until_it_is_resolved(self):
        rule = make_rule()
        user = make_user("alice")
        case = TaxArrearsCase.objects.create(user=user, rule=rule)
        self.assertTrue(case.is_active)
        self.assertEqual(str(case), "alice / storage.gb_day — open")
        case.status = ArrearsStatus.RESOLVED
        self.assertFalse(case.is_active)

    def test_a_grant_names_its_scope(self):
        user = make_user("alice")
        self.assertEqual(str(TimeGrant(user=user, key="k", seconds=5)),
                         "alice / k (account) = 5s")
        self.assertEqual(str(TimeGrant(user=user, key="k", scope_id=9, seconds=5)),
                         "alice / k (#9) = 5s")
