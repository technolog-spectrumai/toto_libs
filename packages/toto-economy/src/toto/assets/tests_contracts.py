"""The contract is what makes an asset a currency — per platform.

Also covers reassignment: a new contract at serial+1 supersedes, old balances
stay spendable, and an asset never contracted can never acquire one.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import override_settings

from toto.assets import contracts
from toto.assets.contracts import (assign_contract, contracted_assets,
                                   contractual_asset, import_contract,
                                   local_contract, may_hold, node_id)
from toto.assets.issuer import NotTheMaster
from toto.assets.models import CurrencyContract, CurrencyIssuer
from toto.assets.testing import LedgerTestCase as TestCase, make_asset


class NodeIdentityTests(TestCase):
    @override_settings(CLEARING_SELF_PLATFORM_ID="zenobia")
    def test_the_platform_names_itself(self):
        self.assertEqual(node_id(), "zenobia")


class AssignTests(TestCase):
    def setUp(self):
        self.asset = make_asset(unit_name="ASR")
        self.other = make_asset(unit_name="TPLN")

    def test_the_master_self_contracts(self):
        contract = assign_contract(node=node_id(), asset=self.asset)

        self.assertTrue(contract.is_local)
        self.assertEqual(contract.serial, 1)
        self.assertEqual(contractual_asset(), self.asset)

    def test_currency_is_a_role_not_a_property(self):
        # The same asset: this platform's currency, and nothing to another.
        assign_contract(node="zenobia", asset=self.asset)
        self.assertTrue(self.asset.is_currency_for("zenobia"))
        self.assertFalse(self.asset.is_currency_for("placidia"))
        self.assertFalse(self.other.is_currency_for("zenobia"))

    def test_an_asset_without_a_hash_can_never_be_a_currency(self):
        # The one way a non-currency could become money.
        self.asset.currency_hash = ""
        with self.assertRaises(ValidationError) as caught:
            assign_contract(node="somewhere", asset=self.asset)
        self.assertIn("no genesis hash", "; ".join(caught.exception.messages))

    def test_a_branch_cannot_assign_anything(self):
        CurrencyIssuer.objects.filter(is_self=True).update(
            private_key_encrypted=None)
        with self.assertRaises(NotTheMaster):
            assign_contract(node="placidia", asset=self.asset)

    def test_only_one_active_local_contract_exists(self):
        assign_contract(node=node_id(), asset=self.asset)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CurrencyContract.objects.create(
                    node_id=node_id(), asset=self.other,
                    currency_hash=self.other.currency_hash,
                    issuer=self.other.issuer, serial=99, is_local=True)


class ReassignTests(TestCase):
    def setUp(self):
        self.old = make_asset(unit_name="OLD")
        self.new = make_asset(unit_name="NEW")
        assign_contract(node=node_id(), asset=self.old)

    def test_a_new_contract_supersedes_the_old_one(self):
        assign_contract(node=node_id(), asset=self.new)

        self.assertEqual(contractual_asset(), self.new)
        self.assertEqual(local_contract().serial, 2)
        self.assertEqual(
            CurrencyContract.objects.filter(superseded_at__isnull=False).count(), 1)

    def test_a_stale_serial_is_refused(self):
        assign_contract(node=node_id(), asset=self.new)
        with self.assertRaises(ValidationError) as caught:
            assign_contract(node=node_id(), asset=self.old, serial=1)
        self.assertIn("not newer", "; ".join(caught.exception.messages))

    def test_an_equal_serial_is_refused_too(self):
        with self.assertRaises(ValidationError):
            assign_contract(node=node_id(), asset=self.new, serial=1)

    def test_the_old_currency_stays_holdable(self):
        # Balances in a retired currency are still real money; what stops is
        # new pricing, because that reads contractual_asset().
        assign_contract(node=node_id(), asset=self.new)

        self.assertTrue(may_hold(self.old))
        self.assertTrue(may_hold(self.new))
        self.assertEqual(
            {a.unit_name for a in contracted_assets()}, {"OLD", "NEW"})

    def test_an_asset_never_contracted_may_not_be_held(self):
        stranger = make_asset(unit_name="STRANGER")
        self.assertFalse(may_hold(stranger))

    def test_nothing_prices_in_the_old_currency_afterwards(self):
        from toto.tariffs.rate_card import gas_asset

        assign_contract(node=node_id(), asset=self.new)
        self.assertEqual(gas_asset(), self.new)


class GasAssetResolutionTests(TestCase):
    @override_settings(GAS_ASSET="TICKER")
    def test_the_contract_beats_the_ticker(self):
        from toto.tariffs.rate_card import gas_asset

        make_asset(unit_name="TICKER")
        contracted = make_asset(unit_name="REAL")
        assign_contract(node=node_id(), asset=contracted)

        self.assertEqual(gas_asset(), contracted)

    @override_settings(GAS_ASSET="TICKER")
    def test_without_a_contract_the_seed_hint_still_resolves(self):
        # A host mid-way through seeding has no contract yet and must still
        # be able to hang prices on something.
        from toto.tariffs.rate_card import gas_asset

        seeded = make_asset(unit_name="TICKER")
        self.assertEqual(gas_asset(), seeded)


class ImportTests(TestCase):
    """Branch side. Everything consequential is checked before anything writes."""

    def setUp(self):
        self.asset = make_asset(unit_name="ASR")
        self.descriptor = contracts.build_contract_descriptor(
            node="placidia", asset=self.asset, serial=1)
        self.descriptor["genesis_signature"] = self.asset.genesis_signature

    def _become_branch(self):
        CurrencyIssuer.objects.filter(is_self=True).update(
            private_key_encrypted=None)

    @override_settings(CLEARING_SELF_PLATFORM_ID="placidia")
    def test_a_master_refuses_to_accept_a_contract(self):
        with self.assertRaises(ValidationError) as caught:
            import_contract(self.descriptor)
        self.assertIn("issues its own currency",
                      "; ".join(caught.exception.messages))

    @override_settings(CLEARING_SELF_PLATFORM_ID="somewhere-else")
    def test_a_contract_for_another_platform_is_refused(self):
        self._become_branch()
        with self.assertRaises(ValidationError) as caught:
            import_contract(self.descriptor)
        self.assertIn("placidia", "; ".join(caught.exception.messages))

    @override_settings(CLEARING_SELF_PLATFORM_ID="placidia")
    def test_a_tampered_genesis_is_refused_and_writes_nothing(self):
        from toto.assets.models import Asset

        self._become_branch()
        Asset.objects.all().delete()
        CurrencyContract.objects.all().delete()
        bad = dict(self.descriptor)
        bad["genesis"] = dict(bad["genesis"], max_supply_base_units=1)

        with self.assertRaises(ValidationError):
            import_contract(bad)
        self.assertEqual(Asset.objects.count(), 0)
        self.assertEqual(CurrencyContract.objects.count(), 0)

    @override_settings(CLEARING_SELF_PLATFORM_ID="placidia")
    def test_replaying_an_old_contract_is_refused(self):
        from toto.assets.models import Asset

        self._become_branch()
        Asset.objects.all().delete()
        CurrencyContract.objects.all().delete()

        import_contract(self.descriptor)
        with self.assertRaises(ValidationError) as caught:
            import_contract(self.descriptor)
        self.assertIn("not newer", "; ".join(caught.exception.messages))
