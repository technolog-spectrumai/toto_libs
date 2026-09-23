"""The read side, one operation at a time: balances, history, series, plain
files, the next refill, and the refusal sentence."""

from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from toto.mana import services
from toto.mana.tests.fixtures import MASTER, economy, held, seed_prices, spend
from toto.tax.tests.factories import GB, make_vault_file

User = get_user_model()


@override_settings(**MASTER)
class ServiceTestCase(TestCase):
    def setUp(self):
        economy()
        self.ada = User.objects.create_user("ada", password="pw")


class BindingTests(ServiceTestCase):
    def test_roles_bind_to_the_three_colours(self):
        self.assertEqual({r: a.unit_name for r, a in services.roles().items()},
                         {"security": "BLUE", "compute": "RED", "storage": "GREEN"})

    def test_a_mapped_metric_resolves_to_its_pool_asset(self):
        self.assertEqual(services.asset_for("storage.request").unit_name, "GREEN")
        self.assertIsNone(services.asset_for("subscription.month"))

    def test_pooled_codes_follow_the_pools_that_exist(self):
        from toto.mana.models import ManaPool

        self.assertIn("antivirus.scan", services.pooled_codes())
        ManaPool.objects.filter(role="security").delete()
        self.assertNotIn("antivirus.scan", services.pooled_codes())
        self.assertIn("storage.request", services.pooled_codes())

    def test_is_mana_asset_names_the_role(self):
        blue = services.roles()["security"]
        self.assertEqual(services.is_mana_asset(blue), "security")
        from toto.assets.models import Asset

        self.assertIsNone(services.is_mana_asset(Asset.objects.get(unit_name="ASR")))


class BalanceTests(ServiceTestCase):
    def test_the_shape_the_chip_and_pages_read(self):
        b = services.balances_of(self.ada)
        self.assertEqual(list(b), ["security", "compute", "storage"])
        row = b["compute"]
        self.assertEqual((row["amount"], row["max"], row["pct"], row["band"]),
                         (Decimal("100"), Decimal("100"), 100, "ok"))
        self.assertEqual(row["regen_per_day"], Decimal("96"))

    def test_anonymous_gets_nothing(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertIsNone(services.balances_of(AnonymousUser()))

    def test_the_lowest_pool_is_the_one_shown(self):
        spend(self.ada, "storage", "60")
        self.assertEqual(services.lowest(services.balances_of(self.ada)), "storage")

    def test_plaintext_shows_as_a_daily_drain_and_an_eta(self):
        seed_prices()
        make_vault_file(self.ada, 5 * GB)             # 5 × 20 = 100 a day, regen 96
        b = services.balances_of(self.ada)["security"]
        self.assertEqual(b["drain_per_day"], Decimal("100"))
        self.assertEqual(b["net_per_day"], Decimal("-4"))
        self.assertEqual(b["trend"], "down")
        self.assertEqual(b["eta_empty_hours"], 600)


class HistoryTests(ServiceTestCase):
    def test_newest_first_with_signed_amounts_and_kinds(self):
        spend(self.ada, "compute", "7")
        rows = services.history(self.ada, "compute")
        self.assertEqual([r["delta"] for r in rows], [Decimal("-7"), Decimal("100")])
        self.assertEqual([r["kind"] for r in rows], ["transfer", "signup"])

    def test_a_charge_is_found_although_its_transaction_names_no_asset(self):
        """Tariff charges leave LedgerTransaction.asset empty; history reads
        LedgerEntry.asset, or the charge would silently vanish."""
        from toto.quota.charge import charge, price_for

        seed_prices()
        charge(self.ada, price_for(self.ada, "vault"), "storage.request", 1)
        rows = services.history(self.ada, "storage")
        self.assertEqual(rows[0]["kind"], "charge")
        self.assertEqual(rows[0]["delta"], Decimal("-0.5"))
        self.assertEqual(rows[0]["metric_code"], "storage.request")

    def test_one_role_only_or_all_of_them(self):
        self.assertEqual({r["role"] for r in services.history(self.ada)},
                         {"security", "compute", "storage"})


class SeriesTests(ServiceTestCase):
    def test_seven_days_ending_at_today_s_level(self):
        spend(self.ada, "storage", "30")
        points = services.series(self.ada, "storage")
        self.assertEqual(len(points), 8)
        self.assertEqual(points[-1]["level"], Decimal("70"))
        self.assertEqual(points[0]["level"], Decimal("0"))    # before the signup fill


class PlainFileTests(ServiceTestCase):
    def test_costliest_first_and_only_plaintext(self):
        from toto.vault.models import VaultFile

        seed_prices()
        small, big = make_vault_file(self.ada, GB // 4), make_vault_file(self.ada, GB)
        sealed = make_vault_file(self.ada, 3 * GB)
        VaultFile.objects.filter(pk=sealed.pk).update(is_encrypted=True)
        rows, total = services.plain_files(self.ada)
        self.assertEqual([r["pk"] for r in rows], [big.pk, small.pk])
        self.assertEqual(total, 2)
        self.assertEqual(rows[0]["drain_per_day"], Decimal("20"))


class TickTests(ServiceTestCase):
    def test_the_next_refill_is_the_coming_minute_thirteen(self):
        at = datetime(2026, 9, 23, 10, 5, tzinfo=dt_timezone.utc)
        self.assertEqual(services.next_tick_at(at), at.replace(minute=13))
        later = at.replace(minute=40)
        self.assertEqual(services.next_tick_at(later),
                         (later + timedelta(hours=1)).replace(minute=13))


class ShortfallTests(ServiceTestCase):
    def test_it_names_the_pool_and_the_wait(self):
        red = services.roles()["compute"]
        text = services.explain_shortfall(self.ada, red, 5 * 10 ** 9, 1 * 10 ** 9)
        self.assertIn("compute mana", text)
        self.assertIn("needs 5 and you have 1", text)
        self.assertIn("about 1 h", text)

    def test_non_mana_assets_are_not_explained(self):
        from toto.assets.models import Asset

        asr = Asset.objects.get(unit_name="ASR")
        self.assertIsNone(services.explain_shortfall(self.ada, asr, 5, 1))

    def test_amounts_read_cleanly(self):
        self.assertEqual(services._amount(Decimal("100.000000000")), "100")
        self.assertEqual(services._amount(Decimal("0.2500")), "0.25")
