"""Tests for the quota core.

    manage.py test toto.quota.tests

`toto` is a PEP 420 namespace package, so the runner cannot discover this by
label — it has to be named. The concrete models below exist only for this
module: quota itself ships no tables, and declaring a pair here is exactly the
opt-in a real app performs.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from toto.quota import (
    QuotaExceeded,
    check_quota,
    get_policy,
    period_start,
    record_usage,
    remaining,
    usage_summary,
    used,
)
from toto.quota.models import (
    AbstractQuotaPolicy,
    AbstractUsageEvent,
    EventStatus,
    Mode,
    Period,
)

User = get_user_model()


class SampleUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        app_label = "quota"


class SampleQuotaPolicy(AbstractQuotaPolicy):
    events = SampleUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        app_label = "quota"


class SampleModels(TestCase):
    """Builds the sample tables around each test class.

    The runner only auto-creates tables for apps with no migrations, and quota
    has two — so these test-only concretes need the schema editor. Done before
    the class atomic opens, and undone after it closes.
    """

    @classmethod
    def setUpClass(cls):
        with connection.schema_editor() as editor:
            editor.create_model(SampleUsageEvent)
            editor.create_model(SampleQuotaPolicy)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        with connection.schema_editor() as editor:
            editor.delete_model(SampleQuotaPolicy)
            editor.delete_model(SampleUsageEvent)


def a_policy(**kwargs):
    kwargs.setdefault("metric_code", "widgets.made")
    kwargs.setdefault("limit", Decimal("10"))
    kwargs.setdefault("period", Period.DAILY)
    kwargs.setdefault("mode", Mode.BLOCK)
    return SampleQuotaPolicy.objects.create(**kwargs)


def an_event(user=None, **kwargs):
    kwargs.setdefault("metric_code", "widgets.made")
    kwargs.setdefault("quantity", Decimal("1"))
    return SampleUsageEvent.objects.create(user=user, **kwargs)


class PolicyResolutionTests(SampleModels):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pw")
        self.bob = User.objects.create_user(username="bob", password="pw")

    def test_no_policy_means_unmetered(self):
        # The free tier is an absence, not a number.
        self.assertIsNone(get_policy(SampleQuotaPolicy, "widgets.made", self.alice))
        self.assertIsNone(remaining(SampleQuotaPolicy, "widgets.made", self.alice))

    def test_default_policy_applies_to_everyone(self):
        default = a_policy()
        self.assertEqual(get_policy(SampleQuotaPolicy, "widgets.made", self.alice), default)
        self.assertEqual(get_policy(SampleQuotaPolicy, "widgets.made", self.bob), default)

    def test_a_users_own_policy_wins(self):
        a_policy()
        mine = a_policy(user=self.alice, limit=Decimal("99"))
        self.assertEqual(get_policy(SampleQuotaPolicy, "widgets.made", self.alice), mine)
        self.assertEqual(get_policy(SampleQuotaPolicy, "widgets.made", self.bob).limit,
                         Decimal("10"))

    def test_inactive_and_expired_policies_are_ignored(self):
        policy = a_policy(active=False)
        self.assertIsNone(get_policy(SampleQuotaPolicy, "widgets.made", self.alice))

        policy.active = True
        policy.ends_at = timezone.now() - timedelta(hours=1)
        policy.save()
        self.assertIsNone(get_policy(SampleQuotaPolicy, "widgets.made", self.alice))

        policy.ends_at = None
        policy.starts_at = timezone.now() + timedelta(hours=1)
        policy.save()
        self.assertIsNone(get_policy(SampleQuotaPolicy, "widgets.made", self.alice))


class EnforcementTests(SampleModels):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pw")

    def test_unmetered_metric_never_raises(self):
        for _ in range(50):
            an_event(self.alice)
        check_quota(SampleQuotaPolicy, "widgets.made", 1, self.alice)   # no policy, no limit

    def test_blocks_only_past_the_limit(self):
        a_policy(limit=Decimal("3"))
        for _ in range(3):
            check_quota(SampleQuotaPolicy, "widgets.made", 1, self.alice)
            an_event(self.alice)

        with self.assertRaises(QuotaExceeded) as caught:
            check_quota(SampleQuotaPolicy, "widgets.made", 1, self.alice)
        self.assertEqual(caught.exception.status_code, 429)
        self.assertIn("widgets.made", str(caught.exception))

    def test_consuming_exactly_the_limit_is_allowed(self):
        # The check is prospective: would THIS request break it?
        a_policy(limit=Decimal("5"))
        check_quota(SampleQuotaPolicy, "widgets.made", 5, self.alice)
        an_event(self.alice, quantity=Decimal("5"))
        with self.assertRaises(QuotaExceeded):
            check_quota(SampleQuotaPolicy, "widgets.made", 1, self.alice)

    def test_track_and_warn_modes_do_not_block(self):
        for mode in (Mode.TRACK, Mode.WARN):
            with self.subTest(mode=mode):
                SampleQuotaPolicy.objects.all().delete()
                SampleUsageEvent.objects.all().delete()
                a_policy(limit=Decimal("1"), mode=mode)
                an_event(self.alice, quantity=Decimal("100"))
                check_quota(SampleQuotaPolicy, "widgets.made", 1, self.alice)

    def test_one_users_usage_does_not_count_against_another(self):
        bob = User.objects.create_user(username="bob", password="pw")
        a_policy(limit=Decimal("2"))
        an_event(bob, quantity=Decimal("99"))
        check_quota(SampleQuotaPolicy, "widgets.made", 1, self.alice)

    def test_voided_events_stop_counting(self):
        a_policy(limit=Decimal("2"))
        event = an_event(self.alice, quantity=Decimal("2"))
        with self.assertRaises(QuotaExceeded):
            check_quota(SampleQuotaPolicy, "widgets.made", 1, self.alice)

        event.status = EventStatus.VOIDED
        event.save()
        check_quota(SampleQuotaPolicy, "widgets.made", 1, self.alice)


class PeriodTests(SampleModels):
    """Windows are calendar-aligned, so a limit actually resets."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pw")

    def test_lifetime_has_no_start(self):
        self.assertIsNone(period_start(Period.LIFETIME))

    def test_daily_starts_at_midnight(self):
        start = period_start(Period.DAILY)
        self.assertEqual((start.hour, start.minute, start.second), (0, 0, 0))

    def test_weekly_starts_on_monday(self):
        self.assertEqual(period_start(Period.WEEKLY).weekday(), 0)

    def test_monthly_starts_on_the_first(self):
        start = period_start(Period.MONTHLY)
        self.assertEqual(start.day, 1)
        self.assertEqual((start.hour, start.minute), (0, 0))

    def test_yearly_starts_in_january(self):
        start = period_start(Period.YEARLY)
        self.assertEqual((start.month, start.day), (1, 1))

    def test_yesterdays_usage_does_not_count_against_a_daily_limit(self):
        a_policy(limit=Decimal("2"), period=Period.DAILY)
        an_event(self.alice, quantity=Decimal("50"),
                 occurred_at=timezone.now() - timedelta(days=1, hours=2))
        self.assertEqual(used(SampleQuotaPolicy, "widgets.made", self.alice,
                              period=Period.DAILY), Decimal("0"))
        check_quota(SampleQuotaPolicy, "widgets.made", 1, self.alice)

    def test_lifetime_counts_everything(self):
        an_event(self.alice, quantity=Decimal("7"),
                 occurred_at=timezone.now() - timedelta(days=900))
        self.assertEqual(used(SampleQuotaPolicy, "widgets.made", self.alice,
                              period=Period.LIFETIME), Decimal("7"))


class RecordingTests(SampleModels):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pw")

    def test_records_and_counts(self):
        record_usage(SampleUsageEvent, "widgets.made", 3, self.alice)
        self.assertEqual(used(SampleQuotaPolicy, "widgets.made", self.alice), Decimal("3"))

    def test_idempotency_key_prevents_a_second_record(self):
        first = record_usage(SampleUsageEvent, "widgets.made", 1, self.alice,
                             idempotency_key="run:42")
        second = record_usage(SampleUsageEvent, "widgets.made", 1, self.alice,
                              idempotency_key="run:42")
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(SampleUsageEvent.objects.count(), 1)

    def test_blank_keys_do_not_collide(self):
        # Only a non-empty key is unique — the constraint is partial.
        for _ in range(3):
            record_usage(SampleUsageEvent, "widgets.made", 1, self.alice)
        self.assertEqual(SampleUsageEvent.objects.count(), 3)

    def test_recording_never_raises(self):
        # Metering must not be able to break the request that triggered it.
        self.assertIsNone(record_usage(SampleUsageEvent, "widgets.made", "not-a-number",
                                       self.alice))

    def test_deleting_a_user_takes_their_usage(self):
        record_usage(SampleUsageEvent, "widgets.made", 1, self.alice)
        self.alice.delete()
        self.assertEqual(SampleUsageEvent.objects.count(), 0)


class SummaryTests(SampleModels):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pw")

    def test_lists_policed_and_used_metrics(self):
        a_policy(metric_code="widgets.made", limit=Decimal("10"))
        record_usage(SampleUsageEvent, "widgets.made", 4, self.alice)
        record_usage(SampleUsageEvent, "gadgets.bent", 2, self.alice)

        rows = {r["metric_code"]: r for r in usage_summary(SampleQuotaPolicy, self.alice)}
        self.assertEqual(rows["widgets.made"]["total"], Decimal("4"))
        self.assertEqual(rows["widgets.made"]["limit"], Decimal("10"))
        self.assertEqual(rows["widgets.made"]["pct_used"], 40.0)
        # Used but unpoliced still shows, with no limit.
        self.assertIsNone(rows["gadgets.bent"]["limit"])
        self.assertIsNone(rows["gadgets.bent"]["pct_used"])

    def test_remaining_headroom(self):
        a_policy(limit=Decimal("10"))
        record_usage(SampleUsageEvent, "widgets.made", 4, self.alice)
        self.assertEqual(remaining(SampleQuotaPolicy, "widgets.made", self.alice), Decimal("6"))

    def test_remaining_never_goes_negative(self):
        a_policy(limit=Decimal("2"))
        record_usage(SampleUsageEvent, "widgets.made", 9, self.alice)
        self.assertEqual(remaining(SampleQuotaPolicy, "widgets.made", self.alice), Decimal("0"))


class ChargeFacadeTests(TestCase):
    """The library must work whether or not the host carries a ledger.

    On a host with no billing app every entry point is a safe no-op, which is
    what lets aurelian run this same code unbilled. On a host that has one, the
    facade forwards. Both are exercised here — which side runs depends on the
    build, so neither assertion may assume the other's world.
    """

    @property
    def billing(self) -> bool:
        from django.apps import apps

        return apps.is_installed("toto.tariffs")

    def test_billing_enabled_reflects_the_app_registry(self):
        from toto.quota import charge

        self.assertEqual(charge.billing_enabled(), self.billing)

    def test_an_unpriced_metric_is_free_either_way(self):
        # No tariff → every entry point is a no-op. This holds on both kinds of
        # host: "billing is absent" and "nothing is priced" are the same answer
        # to a caller, which is the whole point of the sentinel.
        from toto.quota import charge

        user = User.objects.create_user(username="alice", password="pw")
        tariff = charge.price_for(user, "widgets") if not self.billing else None
        self.assertIsNone(tariff)
        self.assertIsNone(charge.check_funds(user, tariff, "widgets.made", 1))
        self.assertIsNone(charge.charge(user, tariff, "widgets.made", 1))
        self.assertIsNone(charge.check_and_charge(user, tariff, "widgets.made", 1))
        self.assertIsNone(charge.refund(None))

    def test_the_exception_is_catchable_and_carries_402(self):
        # Whether this is the real exception or the stub, a call site must be
        # able to write `except InsufficientFunds` and read `.status_code`.
        # Which one is bound depends on whether the host *ships* tariffs, not
        # on whether it installs it — the two have different constructors.
        from toto.quota.charge import InsufficientFunds

        self.assertEqual(InsufficientFunds.status_code, 402)
        try:
            instance = InsufficientFunds("ASR", 100, 1, 9)   # the real one
        except TypeError:
            instance = InsufficientFunds("broke")            # the stub
        with self.assertRaises(InsufficientFunds):
            raise instance
