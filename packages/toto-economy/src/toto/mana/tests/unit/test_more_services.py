"""The mana read side at its edges: the refusal sentence's boundaries, the
balances a member sees (circle speed, a zero circle, the community discount on
the drain, one circle read), how movements are named, and every function's
answer on a host with no pools yet.
"""

import shutil
import tempfile
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from toto.mana import services
from toto.mana.models import ManaPool
from toto.mana.tests.fixtures import economy, master, seed_prices, spend
from toto.people.models import Person
from toto.socialhub.models import Community
from toto.subscriptions.models import CommunityDiscount
from toto.tax.tests.factories import GB, make_vault_file

User = get_user_model()


class TempMediaMixin:
    """``make_vault_file`` writes a real file: keep it out of the shared MEDIA_ROOT."""

    @classmethod
    def setUpClass(cls):
        media = tempfile.mkdtemp(prefix="mana-test-media-")
        cls.addClassCleanup(shutil.rmtree, media, ignore_errors=True)
        setting = override_settings(MEDIA_ROOT=media)
        setting.enable()
        cls.addClassCleanup(setting.disable)
        super().setUpClass()


def circle(name, **speeds):
    return Community.objects.create(
        name=name, is_circle=True, **{f"regen_{role}": value for role, value in speeds.items()})


def join(user, *communities):
    person, _ = Person.objects.get_or_create(user=user, defaults={"display_name": user.username})
    person.communities.add(*communities)


@master
class ShortfallSentenceTests(TestCase):
    def setUp(self):
        economy()
        self.pools = services.pools()
        self.red = self.pools["compute"].asset
        self.scale = 10 ** self.red.decimals
        self.ada = User.objects.create_user("ada", password="pw")

    def explain(self, needed, have, user=None):
        return services.explain_shortfall(user or self.ada, self.red,
                                          int(Decimal(needed) * self.scale),
                                          int(Decimal(have) * self.scale))

    def test_the_wait_is_rounded_up_to_whole_hours(self):
        self.assertIn("enough again in about 3 h", self.explain("10", "1"))     # 9 / 4

    def test_a_tiny_shortfall_still_says_one_hour(self):
        self.assertIn("about 1 h", self.explain("1", "0.99"))

    def test_exactly_a_full_pool_can_still_be_waited_for(self):
        text = self.explain("100", "0")
        self.assertNotIn("more than a full pool", text)
        self.assertIn("about 25 h", text)

    def test_just_over_a_full_pool_cannot(self):
        text = self.explain("100.01", "0")
        self.assertIn("more than a full pool holds (100)", text)
        self.assertNotIn("refills", text)

    def test_a_member_whose_circle_stops_the_refill_is_promised_no_wait(self):
        join(self.ada, circle("paused", compute=Decimal("0")))
        text = self.explain("5", "1")
        self.assertTrue(text.endswith("you have 1."), text)
        self.assertNotIn("refills", text)

    def test_a_switched_off_pool_promises_no_wait(self):
        ManaPool.objects.filter(role="compute").update(regen_per_hour=Decimal("0"))
        self.assertNotIn("refills", self.explain("5", "1"))

    def test_amounts_are_cut_to_two_places_never_rounded_up(self):
        text = self.explain("0.129", "0.001")
        self.assertIn("needs 0.12 and you have 0", text)

    def test_each_pool_names_itself(self):
        for role, words in (("security", "security mana"), ("storage", "storage mana")):
            asset = self.pools[role].asset
            with self.subTest(role=role):
                self.assertIn(words, services.explain_shortfall(self.ada, asset, 5 * self.scale, 0))


class AmountAndKindTests(SimpleTestCase):
    def test_whole_numbers_are_bare_and_fractions_lose_trailing_zeros(self):
        self.assertEqual(services._amount(Decimal("7")), "7")
        self.assertEqual(services._amount(Decimal("1.10")), "1.1")
        self.assertEqual(services._amount(Decimal("0.005")), "0")

    def tx(self, reference="", source_type="", metadata=None, reversed_id=None):
        return SimpleNamespace(reference=reference, source_type=source_type,
                               metadata=metadata, reversed_transaction_id=reversed_id)

    def test_every_movement_is_named(self):
        cases = [
            (self.tx("mana:reward:encrypt:4:2026-09-29"), "reward"),
            (self.tx("mana:compute:3:signup"), "signup"),
            (self.tx("mana:compute:3:hourly:2026-09-29T10"), "regen"),
            (self.tx("x", "tariff_usage", {"metric_code": "storage.gb_day"}), "levy"),
            (self.tx("x", "tariff_usage", {"metric_code": "storage.request"}), "charge"),
            (self.tx("refund-1", reversed_id=9), "refund"),
            (self.tx("test-spend-1"), "transfer"),
            (self.tx(None), "transfer"),
        ]
        for tx, kind in cases:
            with self.subTest(kind=kind, reference=tx.reference):
                self.assertEqual(services._kind(tx, "compute"), kind)

    def test_another_pools_refill_is_not_this_pools_regen(self):
        self.assertEqual(services._kind(self.tx("mana:storage:3:hourly:x"), "compute"), "transfer")

    def test_the_lowest_pool_breaks_ties_by_name_and_empty_is_none(self):
        self.assertIsNone(services.lowest(None))
        self.assertIsNone(services.lowest({}))
        balances = {"storage": {"pct": 40, "role": "storage"},
                    "compute": {"pct": 40, "role": "compute"},
                    "security": {"pct": 90, "role": "security"}}
        self.assertEqual(services.lowest(balances), "compute")

    def test_the_labels_and_unknown_roles(self):
        self.assertEqual(services.label_of("storage"), "Storage")
        self.assertEqual(services.label_of("gold"), "gold")
        self.assertEqual(services._label("gold"), "gold")


class NextTickTests(SimpleTestCase):
    def test_exactly_on_the_minute_is_the_next_hour(self):
        at = datetime(2026, 9, 29, 10, 13, tzinfo=dt_timezone.utc)
        self.assertEqual(services.next_tick_at(at), at + timedelta(hours=1))

    @override_settings(MANA_REGEN_MINUTE=0)
    def test_the_minute_is_a_setting(self):
        at = datetime(2026, 9, 29, 10, 5, tzinfo=dt_timezone.utc)
        self.assertEqual(services.next_tick_at(at), datetime(2026, 9, 29, 11, 0, tzinfo=dt_timezone.utc))

    def test_a_local_time_is_answered_in_utc(self):
        warsaw = dt_timezone(timedelta(hours=2))
        at = datetime(2026, 9, 29, 12, 5, tzinfo=warsaw)                 # 10:05 UTC
        tick = services.next_tick_at(at)
        self.assertEqual(tick, datetime(2026, 9, 29, 10, 13, tzinfo=dt_timezone.utc))
        self.assertEqual(tick.utcoffset(), timedelta(0))


@master
class BalancesTests(TestCase):
    def setUp(self):
        economy()
        self.ada = User.objects.create_user("ada", password="pw")

    def test_the_member_speed_and_the_pool_default_are_both_shown(self):
        join(self.ada, circle("board", compute=Decimal("12")))
        row = services.balances_of(self.ada)["compute"]
        self.assertEqual((row["regen_per_hour"], row["regen_circle"], row["regen_default"]),
                         (Decimal("12"), "board", Decimal("4")))
        self.assertEqual(services.balances_of(self.ada)["storage"]["regen_circle"], "")

    def test_a_stopped_member_below_full_has_no_trend_and_no_eta(self):
        join(self.ada, circle("paused", compute=Decimal("0")))
        spend(self.ada, "compute", "30")
        row = services.balances_of(self.ada)["compute"]
        self.assertEqual((row["regen_per_day"], row["trend"]), (Decimal(0), "flat"))
        self.assertEqual((row["eta_full_hours"], row["eta_empty_hours"]), (None, None))

    def test_a_refilling_member_is_told_when_the_pool_is_full(self):
        spend(self.ada, "compute", "30")
        row = services.balances_of(self.ada)["compute"]
        self.assertEqual((row["trend"], row["eta_full_hours"]), ("up", 8))     # 30 / 4 → 8 h
        self.assertEqual(row["band"], "ok")

    def test_the_circles_are_read_once_for_all_three_pools(self):
        with mock.patch.object(services, "circle_speeds", wraps=services.circle_speeds) as read:
            services.balances_of(self.ada)
        read.assert_called_once_with([self.ada.pk])

    def test_a_host_with_no_pools_shows_nothing(self):
        ManaPool.objects.all().delete()
        self.assertIsNone(services.balances_of(self.ada))
        self.assertEqual(services.history(self.ada), [])
        self.assertEqual(services.series(self.ada, "compute"), [])

    def test_a_pool_not_set_up_is_left_out(self):
        ManaPool.objects.filter(role="storage").delete()
        self.assertEqual(list(services.balances_of(self.ada)), ["security", "compute"])

    def test_none_is_nobody(self):
        self.assertIsNone(services.balances_of(None))


@master
class DiscountedDrainTests(TempMediaMixin, TestCase):
    """What the pages say a pool costs a day is what the levy will take —
    after the member's community discount (2026-09-28)."""

    def setUp(self):
        economy()
        seed_prices()
        self.ada = User.objects.create_user("ada", password="pw")
        make_vault_file(self.ada, 5 * GB)                  # 5 × 20 = 100 a day at list

    def discount(self, percent):
        students = Community.objects.create(name="Students")
        CommunityDiscount.objects.create(community=students, percent=percent)
        join(self.ada, students)

    def test_the_list_drain_without_a_discount(self):
        self.assertEqual(services.drain_per_day(self.ada, "security"), Decimal("100"))

    def test_a_discount_lowers_the_drain_and_the_net(self):
        self.discount(50)
        row = services.balances_of(self.ada)["security"]
        self.assertEqual(row["drain_per_day"], Decimal("50"))
        self.assertEqual(row["net_per_day"], Decimal("46"))                     # 96 − 50
        self.assertEqual(row["trend"], "flat")                                  # full already

    def test_a_full_discount_drains_nothing(self):
        self.discount(100)
        self.assertEqual(services.drain_per_day(self.ada, "security"), Decimal("0"))

    def test_the_plain_file_list_quotes_the_member_price(self):
        self.discount(25)
        rows, total = services.plain_files(self.ada)
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["drain_per_day"], Decimal("75"))

    def test_compute_has_no_levy(self):
        self.assertEqual(services.drain_per_day(self.ada, "compute"), Decimal("0"))

    def test_an_unpriced_levy_drains_nothing(self):
        with mock.patch("toto.quota.rates.price_of", return_value=None):
            self.assertEqual(services.drain_per_day(self.ada, "security"), Decimal("0"))
            self.assertEqual(services._price("security.plain_gb_day"), None)

    def test_a_price_per_several_units_is_divided_out(self):
        row = {"price_display": "30", "unit_quantity": 10}
        with mock.patch("toto.quota.rates.price_of", return_value=row):
            self.assertEqual(services._price("security.plain_gb_day"), Decimal("3"))
            self.assertEqual(services._price("security.plain_gb_day", 50), Decimal("1.5"))

    def test_without_the_levy_provider_there_is_no_plain_file_list(self):
        with mock.patch("toto.quota.levy.registry.get", return_value=None):
            self.assertEqual(services.plain_files(self.ada), ([], 0))
            self.assertEqual(services.drain_per_day(self.ada, "security"), Decimal("0"))


@master
class HistoryKindsTests(TestCase):
    def setUp(self):
        economy()
        self.ada = User.objects.create_user("ada", password="pw")
        self.root = User.objects.create_superuser("root", password="pw")

    def test_a_grant_by_hand_reads_as_manual_with_its_reason(self):
        spend(self.ada, "security", "20")
        services.grant_manual(self.ada, services.pools()["security"], 5, reason="Helping out",
                              granted_by=self.root)
        row = services.history(self.ada, "security")[0]
        self.assertEqual((row["kind"], row["reason"], row["delta"]),
                         ("manual", "Helping out", Decimal("5")))
        self.assertEqual(row["faucet"], "Mana security — manual")

    def test_an_hourly_refill_reads_as_regen(self):
        spend(self.ada, "compute", "20")
        services.regenerate_hour(at=datetime(2026, 9, 29, 10, 30, tzinfo=dt_timezone.utc))
        row = services.history(self.ada, "compute")[0]
        self.assertEqual((row["kind"], row["delta"]), ("regen", Decimal("4")))

    def test_the_limit_is_honoured_newest_first(self):
        for n in range(3):
            spend(self.ada, "compute", "1")
        rows = services.history(self.ada, "compute", limit=2)
        self.assertEqual([r["delta"] for r in rows], [Decimal("-1"), Decimal("-1")])
