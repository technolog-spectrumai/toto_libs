"""What an install is guaranteed to have once ANY ingress command has run.

The bug this closes is a dead end rather than a crash. `deploy.py` mints
`MONETARY_ISSUER_KEY` into every host's environment, but nothing ever created
the issuer ROW, so `is_monetary_master()` answered False forever, `ingress_assets`
politely seeded no currency, and the platform sat there with an economy that did
not exist — which is why minting answered 500 and no tariff price could be saved.

Nothing here may depend on `ingress_all`: the seeding used to live in one
command, so an operator who ran any of the other thirty-odd got a working app on
an empty ledger. The hook is on `IngressCommand` itself.
"""
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from toto.assets.models import Asset, LedgerAccount, LedgerTransaction
from toto.assets.services.bootstrap import (CORE_ASSETS, bootstrap_economy,
                                            ensure_core_assets,
                                            ensure_monetary_issuer)
from toto.assets.testing import TEST_ISSUER_KEY

CORE_UNITS = {"MANA", "ASR", "TPLN"}


@override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY, ASSETS_MONETARY_MASTER=True)
class FreshInstallTests(TestCase):
    """Nothing exists yet. One call, and the platform has an economy."""

    def test_it_mints_the_issuer_this_host_never_had(self):
        from toto.assets.issuer import is_monetary_master, local_issuer

        self.assertIsNone(local_issuer())
        bootstrap_economy()
        self.assertIsNotNone(local_issuer())
        self.assertTrue(is_monetary_master())

    def test_it_creates_the_three_core_assets(self):
        bootstrap_economy()
        self.assertEqual(
            set(Asset.objects.values_list("unit_name", flat=True)), CORE_UNITS)

    def test_each_one_is_engraved_with_its_stated_supply_and_scale(self):
        """A maximum cannot be raised afterwards, so this is the only chance to
        get it right — and the numbers live in one place for that reason."""
        bootstrap_economy()
        for core in CORE_ASSETS:
            with self.subTest(unit=core.unit_name):
                asset = Asset.objects.get(unit_name=core.unit_name)
                self.assertEqual(asset.decimals, core.decimals)
                self.assertEqual(asset.max_supply_display, core.supply)

    def test_every_core_asset_has_a_reserve_to_be_paid_out_of(self):
        """Faucets and rewards transfer FROM the reserve, so an asset without
        one is an asset nobody can ever be paid in."""
        bootstrap_economy()
        for unit in CORE_UNITS:
            with self.subTest(unit=unit):
                asset = Asset.objects.get(unit_name=unit)
                self.assertIsNotNone(asset.reserve_account_id)
                self.assertEqual(asset.reserve_account.code, "currency-reserve")

    def test_the_opening_supply_is_actually_in_the_reserve(self):
        bootstrap_economy()
        for core in CORE_ASSETS:
            with self.subTest(unit=core.unit_name):
                self.assertTrue(
                    LedgerTransaction.objects.filter(
                        reference=core.reference).exists())


@override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY, ASSETS_MONETARY_MASTER=True)
class AlreadyInitialisedTests(TestCase):
    """Ingress is re-run on every deploy. A second run must change nothing."""

    def setUp(self):
        bootstrap_economy()

    def test_running_it_again_creates_no_duplicates(self):
        before = list(Asset.objects.order_by("pk").values_list("pk", "unit_name"))
        bootstrap_economy()
        self.assertEqual(
            list(Asset.objects.order_by("pk").values_list("pk", "unit_name")),
            before)

    def test_running_it_again_mints_no_second_supply(self):
        """The failure that would matter most, and the quietest one."""
        before = LedgerTransaction.objects.count()
        bootstrap_economy()
        bootstrap_economy()
        self.assertEqual(LedgerTransaction.objects.count(), before)

    def test_it_mints_no_second_issuer(self):
        from toto.assets.issuer import local_issuer

        first = local_issuer().fingerprint
        bootstrap_economy()
        self.assertEqual(local_issuer().fingerprint, first)

    def test_it_does_not_reset_an_asset_staff_have_edited(self):
        """Bootstrap guarantees existence, never contents.

        Only the mutable half is asserted, because the model refuses the rest
        outright: an asset's identity — its name, scale and ceiling — cannot
        change once issued, so bootstrap could not overwrite those even if it
        tried. What it must not touch is what staff CAN change.
        """
        asset = Asset.objects.get(unit_name="MANA")
        asset.minting_authority = "The Guild"
        asset.active = False
        asset.save(update_fields=["minting_authority", "active"])
        bootstrap_economy()
        asset.refresh_from_db()
        self.assertEqual(asset.minting_authority, "The Guild")
        self.assertFalse(asset.active)

    def test_a_mint_record_cannot_be_removed_to_get_a_second_supply(self):
        """The ledger refuses, which is the guarantee underneath the guard.

        `ensure_core_assets` checks the mint reference as well as the asset row,
        but that second guard only ever has to hold if the first one is bypassed
        — and this is why bypassing it is not something a caller can arrange.
        """
        from django.db.models import ProtectedError

        with self.assertRaises(ProtectedError):
            LedgerTransaction.objects.filter(reference="create-mana").delete()


class NotTheMasterTests(TestCase):
    """A host that must not invent money."""

    @override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY,
                       ASSETS_MONETARY_MASTER=False)
    def test_a_branch_mints_no_issuer_and_seeds_no_currency(self):
        bootstrap_economy()
        self.assertFalse(Asset.objects.exists())

    @override_settings(MONETARY_ISSUER_KEY="", ASSETS_MONETARY_MASTER=True)
    def test_a_host_with_no_issuer_secret_does_nothing_and_does_not_raise(self):
        bootstrap_economy()
        self.assertFalse(Asset.objects.exists())

    @override_settings(MONETARY_ISSUER_KEY="not-a-fernet-key",
                       ASSETS_MONETARY_MASTER=True)
    def test_a_malformed_issuer_secret_is_not_fatal(self):
        """An ingress run for an unrelated app must not die on the economy."""
        self.assertIsNone(ensure_monetary_issuer())
        bootstrap_economy()


@override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY, ASSETS_MONETARY_MASTER=True)
class EveryIngressPathTests(TestCase):
    """Not `ingress_all` alone — that is the whole point of the change.

    The currencies were seeded by one command out of thirty-odd, so whether an
    install had an economy depended on which bootstrap path its operator
    happened to run.
    """

    def test_an_unrelated_ingress_command_still_guarantees_the_currencies(self):
        call_command("ingress_quota", stdout=StringIO(), stderr=StringIO())
        self.assertEqual(
            set(Asset.objects.values_list("unit_name", flat=True)), CORE_UNITS)

    def test_ingress_assets_guarantees_them_too(self):
        call_command("ingress_assets", stdout=StringIO(), stderr=StringIO())
        self.assertTrue(CORE_UNITS.issubset(
            set(Asset.objects.values_list("unit_name", flat=True))))

    def test_two_different_ingress_commands_do_not_duplicate_anything(self):
        call_command("ingress_quota", stdout=StringIO(), stderr=StringIO())
        call_command("ingress_core", stdout=StringIO(), stderr=StringIO())
        for unit in CORE_UNITS:
            with self.subTest(unit=unit):
                self.assertEqual(Asset.objects.filter(unit_name=unit).count(), 1)
