"""Every ingress mints the three colours once and binds the pools once."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from toto.assets.models import Asset, Faucet, LedgerTransaction
from toto.assets.services.bootstrap import (CORE_ASSETS, RESERVE_CODE,
                                            bootstrap_economy)
from toto.assets.services.settlement import settlement_asset
from toto.assets.testing import TEST_ISSUER_KEY, make_asset
from toto.core.models import Platform
from toto.mana.bootstrap import SUPPLY, retire_legacy_mana
from toto.mana.models import ManaPool

MASTER = dict(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY, ASSETS_MONETARY_MASTER=True)


def platform():
    Platform.objects.get_or_create(
        site_name="Test",
        defaults={"author": "t", "publication_year": 2026, "active": True})


@override_settings(**MASTER)
class MintTests(TestCase):
    def setUp(self):
        platform()
        bootstrap_economy()

    def test_three_colours_are_minted_into_the_reserve(self):
        for unit in ("BLUE", "RED", "GREEN"):
            with self.subTest(unit=unit):
                asset = Asset.objects.get(unit_name=unit)
                self.assertTrue(asset.active)
                self.assertEqual(asset.decimals, 9)
                self.assertEqual(asset.reserve_account.code, RESERVE_CODE)
                self.assertEqual(asset.metadata["family"], "toto_mana")

    def test_each_role_is_bound_to_its_colour(self):
        bound = {p.role: p.asset.unit_name
                 for p in ManaPool.objects.select_related("asset")}
        self.assertEqual(bound, {"security": "BLUE", "compute": "RED",
                                 "storage": "GREEN"})

    def test_the_seeded_dials_are_four_an_hour_up_to_a_hundred(self):
        for pool in ManaPool.objects.all():
            self.assertEqual(pool.regen_per_hour, Decimal("4"))
            self.assertEqual(pool.max_pool, Decimal("100"))

    def test_a_second_ingress_mints_nothing(self):
        bootstrap_economy()
        self.assertEqual(Asset.objects.filter(unit_name="BLUE").count(), 1)
        self.assertEqual(
            LedgerTransaction.objects.filter(reference="create-blue").count(), 1)
        self.assertEqual(ManaPool.objects.count(), 3)

    def test_a_staff_edit_survives_the_next_deploy(self):
        pool = ManaPool.objects.get(role="compute")
        pool.regen_per_hour = Decimal("9")
        pool.save()
        bootstrap_economy()
        pool.refresh_from_db()
        self.assertEqual(pool.regen_per_hour, Decimal("9"))

    def test_a_fresh_install_has_no_mana_currency(self):
        self.assertFalse(Asset.objects.filter(unit_name="MANA").exists())
        self.assertNotIn("MANA", [c.unit_name for c in CORE_ASSETS])

    def test_the_platform_settles_in_asr(self):
        self.assertEqual(settlement_asset().unit_name, "ASR")

    def test_supply_is_the_drip_sized_one(self):
        asset = Asset.objects.get(unit_name="GREEN")
        self.assertEqual(asset.max_supply_base_units, int(SUPPLY * 10 ** 9))


@override_settings(**MASTER)
class RetirementTests(TestCase):
    """An existing ledger's MANA is switched off, never deleted."""

    def setUp(self):
        platform()

    def test_the_seeded_mana_and_its_faucet_are_deactivated(self):
        mana = make_asset(unit_name="MANA", decimals=9,
                          metadata={"seeded_by": "ingress"})
        Faucet.objects.create(slug="default-mana", name="Mana faucet",
                              asset=mana, active=True)
        bootstrap_economy()
        mana.refresh_from_db()
        self.assertFalse(mana.active)
        self.assertFalse(Faucet.objects.get(slug="default-mana").active)

    def test_a_hand_minted_mana_is_left_alone(self):
        mana = make_asset(unit_name="MANA", decimals=9, metadata={})
        retire_legacy_mana()
        mana.refresh_from_db()
        self.assertTrue(mana.active)

    def test_retirement_is_idempotent(self):
        make_asset(unit_name="MANA", decimals=9, metadata={"seeded_by": "ingress"})
        self.assertEqual(retire_legacy_mana(), 1)
        self.assertEqual(retire_legacy_mana(), 0)


@override_settings(MONETARY_ISSUER_KEY="", ASSETS_MONETARY_MASTER=False)
class BranchTests(TestCase):
    def test_a_branch_mints_nothing_and_does_not_raise(self):
        platform()
        bootstrap_economy()
        self.assertFalse(Asset.objects.filter(unit_name="BLUE").exists())
        self.assertEqual(ManaPool.objects.count(), 0)
