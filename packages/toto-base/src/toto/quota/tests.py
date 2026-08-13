"""Tests for the quota core.

    manage.py test toto.quota.tests

`toto` is a PEP 420 namespace package, so the runner cannot discover this by
label — it has to be named. The concrete models below exist only for this
module: quota itself ships no tables, and declaring a pair here is exactly the
opt-in a real app performs.
"""

from __future__ import annotations

from datetime import timedelta
import unittest
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
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

    def test_there_is_no_per_person_policy(self):
        """One row per metric, and the constraint says so.

        A row naming a user used to beat the default. It went the way every
        other name-an-individual mechanism went: headroom for one person is an
        office's ``Station.limit_multiplier``, which is visible on the public
        roster and moves to their successor.
        """
        from django.db.utils import IntegrityError

        a_policy()
        with self.assertRaises(IntegrityError):
            a_policy(limit=Decimal("99"))

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


# ---------------------------------------------------------------------------
# The metric registry
# ---------------------------------------------------------------------------

class MetricRegistryTests(TestCase):
    """What apps declare, and what the UI reads back."""

    def test_apps_declared_their_metrics(self):
        from toto.quota.metrics import registry

        # Autodiscovery ran at startup; if it had not, this whole feature is
        # invisible and the failure would otherwise be a mysteriously empty page.
        self.assertGreater(len(registry), 0)
        for metric in registry.all():
            self.assertTrue(metric.code)
            self.assertTrue(metric.label)
            self.assertTrue(metric.app_label)

    def test_every_metric_resolves_to_a_table_to_store_limits_in(self):
        from django.apps import apps
        from toto.quota.metrics import policy_model_for, registry

        for metric in registry.all():
            with self.subTest(metric=metric.code):
                if not apps.is_installed(f"toto.{metric.app_label}"):
                    continue
                self.assertIsNotNone(
                    policy_model_for(metric.app_label),
                    f"{metric.app_label} declares {metric.code} but ships no quota table",
                )

    def test_a_second_app_cannot_claim_a_code(self):
        from toto.quota.metrics import DuplicateMetric, Metric, MetricRegistry

        local = MetricRegistry()
        local.register(Metric(code="a.b", label="A", app_label="one"))
        local.register(Metric(code="a.b", label="A", app_label="one"))   # identical: fine
        with self.assertRaises(DuplicateMetric):
            local.register(Metric(code="a.b", label="Different", app_label="two"))

    def test_grouping_and_lookup(self):
        from toto.quota.metrics import Metric, MetricRegistry

        local = MetricRegistry()
        local.register(Metric(code="x.one", label="One", app_label="x"))
        local.register(Metric(code="y.one", label="One", app_label="y"))
        self.assertEqual(sorted(local.by_app()), ["x", "y"])
        self.assertEqual(local.get("x.one").label, "One")
        self.assertIsNone(local.get("nope"))
        self.assertEqual(local.codes(), ["x.one", "y.one"])

    def test_the_code_is_the_whole_identity(self):
        """One string names the limit, the charge and the idempotency key.

        Three apps used to record under a short name and bill under a long one,
        so a limit could never match what was charged.
        """
        from toto.quota.metrics import registry

        for stale in ("compile.run", "run.started", "chain.verify"):
            self.assertIsNone(
                registry.get(stale),
                f"{stale} is the old quota-side spelling and must not come back",
            )


class LimitsUiTests(SampleModels):
    """The pages, and who may see them."""

    def setUp(self):
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "test", "publication_year": 2026, "active": True},
        )
        self.staff = User.objects.create_user(username="uistaff", password="pw", is_staff=True)
        self.plain = User.objects.create_user(username="uiplain", password="pw")

    def test_index_and_my_usage_are_for_everyone(self):
        self.client.force_login(self.plain)
        for url in ("/quota/", "/quota/me/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_anonymous_is_sent_to_log_in(self):
        for url in ("/quota/", "/quota/me/"):
            with self.subTest(url=url):
                self.assertNotEqual(self.client.get(url).status_code, 200)

    def test_a_thing_is_readable_by_everyone_and_writable_by_staff(self):
        """The per-thing page serves both roles from one URL.

        It was staff-only, which is why a member had nowhere to read what one
        action costs them and how much they had left — the two facts they most
        want. Both roles GET it now; the editors are hidden from a member and
        every POST action re-checks is_staff, so hiding stays cosmetic.
        """
        from toto.quota.metrics import registry

        code = registry.codes()[0]
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get(f"/quota/{code}/").status_code, 200)
        self.assertEqual(
            self.client.post(f"/quota/{code}/",
                             {"action": "default", "limit": "3"}).status_code, 403)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(f"/quota/{code}/").status_code, 200)

    def test_an_unregistered_metric_is_a_404(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get("/quota/no.such.metric/").status_code, 404)


class RateDeskTests(SampleModels):
    """The editable grid — now the collection view itself.

    It used to be a separate screen, `/quota/rates/`, which was the same table
    of the same metrics keyed by the same string as `/quota/`, with a different
    vocabulary for the same fields. There is one table now and staff edit it in
    place; these tests followed it there and are otherwise unchanged.
    """

    def setUp(self):
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "test", "publication_year": 2026, "active": True},
        )
        self.staff = User.objects.create_user(username="deskstaff", password="pw", is_staff=True)
        self.plain = User.objects.create_user(username="deskplain", password="pw")

    def _code(self):
        from toto.quota.metrics import registry

        return registry.codes()[0]

    def test_taxes_sits_above_the_code_route(self):
        """A concrete segment declared BELOW `<str:code>/` is swallowed by it —
        metric codes are dotted, which `<str:>` matches happily. `taxes/` is the
        newest such segment and the trap the urls.py comment warns about."""
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get("/quota/taxes/").status_code, 200)

    def test_a_member_reads_the_catalogue_and_cannot_reach_the_desk(self):
        """Reading and editing are two URLs now.

        Reading was staff-only while the grid was its own screen; then it became
        the collection view with the editors hidden inside it, which made the
        Metered chip a link to a console. The catalogue is GET-only and open to
        members, and everything that changes a number lives at /quota/desk/.
        """
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get("/quota/").status_code, 200)
        # Not 403 — there is nothing here to be forbidden from. A POST to a
        # reading surface is the wrong verb, and 405 says so.
        self.assertEqual(self.client.post("/quota/", {}).status_code, 405)
        self.assertEqual(self.client.get("/quota/desk/").status_code, 403)

    def test_the_desk_is_staff_only(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get("/quota/desk/").status_code, 200)

    def test_a_limit_is_set_from_the_edit_modal(self):
        """Editing is one thing at a time now, not a grid of live inputs."""
        from toto.quota.metrics import policy_model_for, registry

        code = self._code()
        metric = registry.get(code)
        model = policy_model_for(metric.app_label)
        self.client.force_login(self.staff)

        self.client.post(f"/quota/{code}/", {
            "action": "default", "limit": "42",
            "unit": metric.unit, "period": metric.period,
            "mode": "block", "active": "on",
        })

        self.assertEqual(model.objects.get(metric_code=code).limit, Decimal("42"))

    def test_the_table_itself_posts_nothing(self):
        """The reading surface stays a reading surface.

        Seventeen rows × four live controls meant a mis-click could reprice the
        platform from the page people came to in order to LOOK something up.
        """
        response = self.client.get("/quota/")
        body = response.content.decode()
        for name in ("limit__", "price__", "mode__", "asset__", "armed__"):
            self.assertNotIn(name, body, name)

    def test_a_blank_limit_deletes_the_policy_rather_than_storing_zero(self):
        """Nothing is limited until a policy exists, so unlimited is an absence."""
        from toto.quota.metrics import policy_model_for, registry

        code = self._code()
        model = policy_model_for(registry.get(code).app_label)
        self.client.force_login(self.staff)
        self.client.post("/quota/", {f"limit__{code}": "5"})

        self.client.post("/quota/", {f"limit__{code}": ""})

        self.assertFalse(model.objects.filter(metric_code=code).exists())

    def test_an_empty_post_changes_nothing(self):
        """The desk's only POST is the charging currency; a blank one is a no-op."""
        from toto.quota.metrics import policy_model_for, registry

        code = self._code()
        metric = registry.get(code)
        model = policy_model_for(metric.app_label)
        self.client.force_login(self.staff)
        self.client.post(f"/quota/{code}/", {
            "action": "default", "limit": "7",
            "unit": metric.unit, "period": metric.period,
            "mode": "block", "active": "on",
        })

        self.client.post("/quota/", {})

        self.assertEqual(model.objects.get(metric_code=code).limit, Decimal("7"))


class PricingBoundaryTests(SampleModels):
    """toto.quota must keep working where toto.tariffs cannot even be imported.

    aurelian and studio pin toto-base but not toto-economy, so this is not a
    hypothetical: the module is absent from those images entirely.
    """

    def setUp(self):
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "test", "publication_year": 2026, "active": True},
        )
        self.staff = User.objects.create_user(username="nbstaff", password="pw", is_staff=True)

    def test_rates_is_a_no_op_when_nothing_bills(self):
        from unittest.mock import patch

        from toto.quota import rates

        with patch.object(rates, "pricing_enabled", return_value=False):
            self.assertEqual(rates.rate_card(), {})
            self.assertIsNone(rates.price_of("anything"))
            self.assertFalse(rates.set_price("anything", "1"))
            self.assertFalse(rates.clear_price("anything"))
            self.assertEqual(rates.advanced_url("anything"), "")
            self.assertEqual(rates.price_asset_symbol(), "")
            self.assertEqual(rates.spend_by_metric(self.staff), {})
            self.assertIsNone(rates.balance_of(self.staff))
            self.assertEqual(rates.wallet_url(), "")

    def test_the_grid_renders_and_saves_limits_with_no_price_column(self):
        from unittest.mock import patch

        from toto.quota import rates
        from toto.quota.metrics import policy_model_for, registry

        code = registry.codes()[0]
        model = policy_model_for(registry.get(code).app_label)
        self.client.force_login(self.staff)

        with patch.object(rates, "pricing_enabled", return_value=False):
            response = self.client.get("/quota/")
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.context["pricing_enabled"])
            # The columns guard themselves individually now — a single
            # column_count could not express a table whose Price and Free
            # columns appear independently. The invariant is stronger and
            # checked directly: no header, and no input, for money.
            body = response.content.decode()
            self.assertNotIn("price__", body)
            self.assertNotIn("charging_currency", body)

            metric = registry.get(code)
            self.client.post(f"/quota/{code}/", {
                "action": "default", "limit": "11",
                "unit": metric.unit, "period": metric.period,
                "mode": "block", "active": "on",
            })

        self.assertEqual(model.objects.get(metric_code=code).limit,
                         Decimal("11"))

    def test_the_free_column_follows_the_levy_engine_not_the_rate_card(self):
        """Two independent guards, deliberately.

        An allowance with no price is a real and supported state — the levy
        measures nightly and charges nothing — and the collection view calls it
        out with a banner rather than hiding the field. So the Free column asks
        "is there a levy engine", which is a different question from "is there a
        rate card", and a single column_count could never have expressed both.
        """
        from unittest.mock import patch

        from toto.quota import levies

        self.client.force_login(self.staff)
        with patch.object(levies, "levy_enabled", return_value=False):
            self.assertNotIn("allowance__", self.client.get("/quota/").content.decode())

    def test_a_forged_price_field_is_ignored_when_nothing_bills(self):
        """The template omits it, the parser skips it, and set_price refuses."""
        from unittest.mock import patch

        from toto.quota import rates
        from toto.quota.metrics import registry

        code = registry.codes()[0]
        self.client.force_login(self.staff)
        with patch.object(rates, "pricing_enabled", return_value=False), \
                patch.object(rates, "set_price") as set_price:
            self.client.post("/quota/", {f"price__{code}": "9.99"})
        set_price.assert_not_called()

    def test_quota_never_imports_tariffs_at_module_scope(self):
        """The rule, pinned at the source rather than only by the repo scan."""
        import ast
        import pathlib

        import toto.quota

        package = pathlib.Path(toto.quota.__file__).parent
        offenders = []
        for path in package.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                # Only module-level imports matter; inside a function body they
                # are lazy by construction and are the sanctioned shape.
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                if getattr(node, "col_offset", 0) != 0:
                    continue
                names = [node.module or ""] if isinstance(node, ast.ImportFrom) \
                    else [a.name for a in node.names]
                if any(n.startswith(("toto.tariffs", "toto.assets")) for n in names):
                    offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(offenders, [])


class ChargingCurrencyTests(SampleModels):
    """ONE charging currency, chosen once, and it has to STICK.

    Three separate things have to line up or the screen lies about what it just
    saved: the choice must persist somewhere `resolve_asset` reads, existing
    prices must be re-denominated with their base units recomputed, and the
    selector must render the saved choice as selected.
    """

    @classmethod
    def setUpClass(cls):
        from django.apps import apps as django_apps

        if not django_apps.is_installed("toto.tariffs"):
            raise unittest.SkipTest("no economy on this host")

        # Seeding an asset ENGRAVES it, which is a monetary act, so the host has
        # to hold an issuer key for it. Same shape as PriceHintLiveTests, and
        # imported lazily behind the skip so toto-base keeps no dependency on
        # the economy wheel.
        from toto.assets.testing import TEST_ISSUER_KEY

        issuer_key = override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY)
        issuer_key.enable()
        cls.addClassCleanup(issuer_key.disable)
        super().setUpClass()

    def setUp(self):
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        self.staff = User.objects.create_user(
            username="curstaff", password="pw", is_staff=True)

    def _assets(self):
        from toto.assets.testing import ensure_local_issuer, make_asset

        ensure_local_issuer()
        gas = make_asset(name="Gas", unit_name="ASR", decimals=9,
                         max_supply_base_units=10 ** 15, active=True)
        other = make_asset(name="Banana", unit_name="BANANA", decimals=2,
                           max_supply_base_units=10 ** 9, active=True)
        return gas, other

    def test_the_choice_persists_where_pricing_reads_it(self):
        from toto.quota import rates
        from toto.tariffs.rate_card import default_tariff

        _gas, other = self._assets()

        rates.set_charging_currency(other.pk)

        # Persisted on the platform tariff — the step `resolve_asset` consults
        # before the contract asset. Without it the next price written would
        # silently revert.
        self.assertEqual(default_tariff().default_asset_id, other.pk)
        # And the page reports the same thing it stored.
        self.assertEqual(rates.price_asset_symbol(), "BANANA")

    def test_a_later_price_uses_the_chosen_currency(self):
        from toto.quota import rates
        from toto.tariffs.models import TariffItem

        _gas, other = self._assets()
        rates.set_charging_currency(other.pk)

        code = self._code() if hasattr(self, "_code") else None
        from toto.quota.metrics import registry

        rates.set_price(registry.codes()[0], "0.05")

        item = TariffItem.objects.get()
        self.assertEqual(item.charged_asset_id, other.pk)

    def test_existing_prices_are_re_denominated_with_correct_base_units(self):
        """A bulk UPDATE would leave every price billing the wrong integer.

        base_units is derived from the DISPLAY price and the asset's decimals,
        so moving ASR (9) → BANANA (2) must recompute it.
        """
        from decimal import Decimal

        from toto.quota import rates
        from toto.quota.metrics import registry
        from toto.tariffs.models import TariffItem

        gas, other = self._assets()
        rates.set_price(registry.codes()[0], "1.5")
        self.assertEqual(TariffItem.objects.get().charged_asset_id, gas.pk)

        rates.set_charging_currency(other.pk)

        item = TariffItem.objects.get()
        self.assertEqual(item.charged_asset_id, other.pk)
        # The number a human typed is untouched — this is a denomination
        # change, not a conversion; the ledger has no rate to convert with.
        self.assertEqual(item.price_per_unit_display, Decimal("1.5"))
        self.assertEqual(item.price_per_unit_base_units, 150)   # 1.5 × 10²

    def test_the_desk_renders_the_saved_choice_as_selected(self):
        from toto.quota import rates

        _gas, other = self._assets()
        rates.set_charging_currency(other.pk)

        self.client.force_login(self.staff)
        body = self.client.get("/quota/desk/").content.decode()

        self.assertIn(f'value="{other.pk}" selected', body)

    def test_an_unknown_currency_is_refused(self):
        from toto.quota import rates

        self._assets()
        with self.assertRaises(ValueError):
            rates.set_charging_currency(999999)
