"""The demurrage levy end to end: measure, charge, arrears, dial reset."""

import datetime
from decimal import Decimal

from toto.assets.testing import LedgerTestCase as TestCase
from django.utils import timezone

from toto.quota.levy import registry as levy_registry
from toto.quota.times import TimeLimit, registry as time_registry

from .. import arrears, services
from ..models import ArrearsStatus, TaxArrearsCase, TaxUsageEvent, TimeGrant
from .factories import fund_prepaid, make_gas_asset, make_user
from .test_times import RegistrySnapshotMixin

HOUR = 3600


def declare(key, free=0, ceiling=100 * HOUR, scope_model=""):
    time_registry.register(TimeLimit(
        key=key, label=f"Dial {key}", app_label="tax",
        scope="workspace" if scope_model else "user",
        free_seconds=free, ceiling_seconds=ceiling, scope_model=scope_model,
        scope_owner_attr="owner_id",
    ))


def price_time_hold(value="0.25"):
    from toto.quota.metrics import registry
    from toto.tariffs.rate_card import upsert_price

    return upsert_price(registry.get("time.hold"), Decimal(value))


def make_time_rule():
    from ..models import TaxRule

    return TaxRule.objects.create(metric_code="time.hold",
                                  unit_label="h",
                                  active=True)


class ProviderTests(RegistrySnapshotMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.provider = levy_registry.get("time.hold")
        self.alice = make_user("alice")
        declare("test.a", free=HOUR, ceiling=10 * HOUR)
        declare("test.b", free=0, ceiling=5 * HOUR)

    def test_registered_with_the_levy_engine(self):
        self.assertIsNotNone(self.provider)
        self.assertEqual(self.provider.raw_per_unit, HOUR)
        self.assertEqual(self.provider.format_raw(2 * HOUR + 1800), "2.5 h")

    def test_sample_and_measure_sum_extras_with_clamping(self):
        bob = make_user("bob")
        TimeGrant.objects.create(user=self.alice, key="test.a", seconds=3 * HOUR)
        TimeGrant.objects.create(user=self.alice, key="test.b", seconds=99 * HOUR)
        TimeGrant.objects.create(user=bob, key="test.b", seconds=0)  # no extra
        TimeGrant.objects.create(user=bob, key="gone.key", seconds=9 * HOUR)

        samples = dict(self.provider.sample())

        # alice: (3h−1h) + min(99h,5h)−0 = 7h; bob: nothing above free.
        self.assertEqual(samples, {self.alice.pk: 7 * HOUR})
        self.assertEqual(self.provider.measure(self.alice), 7 * HOUR)
        self.assertEqual(self.provider.measure(bob), 0)

    def test_stale_scope_grants_are_pruned(self):
        declare("test.scoped", free=0, ceiling=10 * HOUR,
                scope_model="vault.Bucket")
        TimeGrant.objects.create(user=self.alice, key="test.scoped",
                                 scope_id=424242, seconds=2 * HOUR)

        self.assertEqual(self.provider.measure(self.alice), 0)
        self.assertEqual(TimeGrant.objects.count(), 0)


class TimeLevyEndToEndTests(RegistrySnapshotMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.asset = make_gas_asset(decimals=9)
        self.rule = make_time_rule()
        declare("test.a", free=0, ceiling=100 * HOUR)
        self.alice = make_user("alice")
        TimeGrant.objects.create(user=self.alice, key="test.a", seconds=4 * HOUR)

    def test_unpriced_records_and_charges_nothing(self):
        summary = services.levy_rule(self.rule)

        self.assertEqual(summary.counts, {services.Outcome.FREE: 1})
        event = TaxUsageEvent.objects.get()
        self.assertEqual(event.metric_code, "time.hold")
        self.assertEqual(event.quantity, Decimal("4"))
        self.assertEqual(
            event.idempotency_key,
            f"tax.time.hold:{self.alice.pk}:{timezone.localdate().isoformat()}")

    def test_priced_and_funded_levies(self):
        from toto.tariffs.models import UsageRecord, UsageStatus

        price_time_hold("0.25")   # 4h × 0.25 = 1 ASR
        fund_prepaid(self.alice, self.asset, 10 ** 10)

        summary = services.levy_rule(self.rule)
        self.assertEqual(summary.counts, {services.Outcome.LEVIED: 1})
        self.assertEqual(UsageRecord.objects.get().status, UsageStatus.POSTED)

        again = services.levy_rule(self.rule)
        self.assertEqual(again.counts, {services.Outcome.ALREADY: 1})

        tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        services.levy_rule(self.rule, day=tomorrow)
        self.assertEqual(TaxUsageEvent.objects.count(), 2)

    def test_a_broke_user_is_warned_then_frozen_and_keeps_their_dials(self):
        from .. import arrears

        price_time_hold("0.25")
        fund_prepaid(self.alice, self.asset, 1)

        summary = services.levy_rule(self.rule)

        self.assertEqual(summary.counts, {services.Outcome.FAILED: 1})
        case = TaxArrearsCase.objects.get(user=self.alice)
        self.assertEqual(case.status, ArrearsStatus.WARNED)
        self.assertFalse(arrears.is_frozen(self.alice))

        # Past the deadline, the next failed day changes nothing except that
        # new usage now refuses. The grants are untouched.
        case.deadline_at = timezone.now() - datetime.timedelta(hours=1)
        case.save(update_fields=["deadline_at"])
        tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        services.levy_rule(self.rule, day=tomorrow)

        case.refresh_from_db()
        self.assertEqual(case.status, ArrearsStatus.WARNED)
        self.assertTrue(arrears.is_frozen(self.alice))
        self.assertEqual(TimeGrant.objects.count(), 1)

    def test_resetting_the_dial_resolves_the_case(self):
        price_time_hold("0.25")
        fund_prepaid(self.alice, self.asset, 1)
        services.levy_rule(self.rule)
        self.assertTrue(TaxArrearsCase.objects.filter(
            user=self.alice, status=ArrearsStatus.WARNED).exists())

        TimeGrant.objects.all().delete()  # the user reset their dial
        tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        services.levy_rule(self.rule, day=tomorrow)

        case = TaxArrearsCase.objects.get(user=self.alice)
        self.assertEqual(case.status, ArrearsStatus.RESOLVED)
        self.assertEqual(case.resolution_reason, arrears.REASON_NOTHING_HELD)

    def test_estimate_row_uses_hour_display(self):
        rows = services.estimate_for_user(self.alice)

        row = next(r for r in rows if r["rule"].metric_code == "time.hold")
        self.assertEqual(row["display"], "4 h")
        self.assertEqual(row["billable"], Decimal("4"))

    def test_the_warning_speaks_time_not_storage(self):
        # Review finding: the notices were hardcoded for storage — the time
        # levy must describe its own consequence, never file deletion.
        from toto.events.models import ScheduledEvent
        from toto.people.models import Person

        Person.objects.create(user=self.alice, display_name="alice", slug="alice")
        price_time_hold("0.25")
        fund_prepaid(self.alice, self.asset, 1)

        services.levy_rule(self.rule)

        warning = ScheduledEvent.objects.get()
        self.assertIn("no new raised time limits", warning.description)
        self.assertIn("the ones you have keep working", warning.description)
        self.assertNotIn("deleted", warning.description)
        self.assertNotIn("GB", warning.description)
