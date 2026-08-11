"""Minting on the master must not reach a branch.

This is the requirement that made successive mints affordable in the first
place: if every mint forced a new contract, a new tariff pass, a conversion or
a migration on every branch, nobody would ever mint. So a branch's world is a
currency HASH and a BALANCE — neither of which a mint touches — and the mint
lots behind that balance are deliberately invisible to it.

The tests below assert the absence of consequences, which is a shape worth
being explicit about: each one takes a full "before" snapshot, mints, and
demands the snapshot back unchanged.
"""

from decimal import Decimal

from django.test import override_settings

from toto.assets import contracts
from toto.assets.allocation import (allocate_to_branch, allocated_base_units,
                                    position_account)
from toto.assets.contracts import assign_contract, node_id
from toto.assets.models import (AccountType, CurrencyContract, LedgerAccount)
from toto.assets.queries import get_asset_balance
from toto.assets.services.assets import mirror_asset
from toto.assets.testing import LedgerTestCase as TestCase
from toto.mint.history import supply
from toto.mint.models import CurrencyMintEvent
from toto.mint.services import burn, create_currency, mint

BRANCH = "placidia"


def _account(code, kind=AccountType.RESERVE):
    return LedgerAccount.objects.create(code=code, name=code,
                                        account_type=kind)


class MintDoesNotDisturbBranchesTests(TestCase):
    def setUp(self):
        super().setUp()
        self.reserve = _account("reserve")
        # Engraved at 1000 and only half minted, so there is room to mint
        # again with a branch already contracted and funded.
        self.asset = create_currency(
            name="Assarion", unit_name="ASR", total_supply=Decimal("1000"),
            decimals=2, reserve_account=self.reserve, reference="open")
        burn(asset=self.asset, amount=Decimal("500"), reason="make room")
        self.contract = assign_contract(node=BRANCH, asset=self.asset)
        allocate_to_branch(node=BRANCH, asset=self.asset,
                           amount=Decimal("100"), reference="alloc-1")

    def _snapshot(self):
        contract = CurrencyContract.objects.get(pk=self.contract.pk)
        return {
            "serial": contract.serial,
            "currency_hash": contract.currency_hash,
            "payload": contract.payload,
            "signature": contract.signature,
            "superseded_at": contract.superseded_at,
            "allocated": allocated_base_units(asset=self.asset),
            "position": get_asset_balance(
                self.asset, position_account(node=BRANCH, asset=self.asset)),
            "contracts": CurrencyContract.objects.count(),
        }

    def test_a_mint_changes_no_contract(self):
        before = self._snapshot()

        mint(asset=self.asset, amount=Decimal("400"), reason="more gas")

        self.assertEqual(self._snapshot(), before)
        self.assertEqual(supply(self.asset), 90_000)

    def test_a_mint_does_not_move_the_currency_hash(self):
        # The reason nothing else has to change. The hash commits the maximum
        # and the nonce, never the amount outstanding.
        before = self.asset.currency_hash

        mint(asset=self.asset, amount=Decimal("100"), reason="more gas")
        self.asset.refresh_from_db()

        self.assertEqual(self.asset.currency_hash, before)
        self.assertTrue(self.asset.verify_genesis())

    def test_a_branch_never_needs_a_new_contract_after_a_mint(self):
        descriptor_before = contracts.build_contract_descriptor(
            node=BRANCH, asset=self.asset, serial=self.contract.serial)

        mint(asset=self.asset, amount=Decimal("100"), reason="more gas")
        self.asset.refresh_from_db()
        descriptor_after = contracts.build_contract_descriptor(
            node=BRANCH, asset=self.asset, serial=self.contract.serial)

        # issued_at is a clock reading, not a fact about the currency.
        for key in ("currency_hash", "genesis", "genesis_signature",
                    "issuer_public_key_pem", "serial", "node_id"):
            self.assertEqual(descriptor_before[key], descriptor_after[key], key)

    def test_a_mint_does_not_change_what_the_branch_holds(self):
        before = get_asset_balance(
            self.asset, position_account(node=BRANCH, asset=self.asset))

        mint(asset=self.asset, amount=Decimal("400"), reason="more gas")

        self.assertEqual(
            get_asset_balance(
                self.asset, position_account(node=BRANCH, asset=self.asset)),
            before)

    def test_allocating_afterwards_is_still_only_a_transfer(self):
        mint(asset=self.asset, amount=Decimal("400"), reason="more gas")
        before = supply(self.asset)
        events = CurrencyMintEvent.objects.count()

        allocate_to_branch(node=BRANCH, asset=self.asset,
                           amount=Decimal("200"), reference="alloc-2")

        self.assertEqual(supply(self.asset), before)
        self.assertEqual(CurrencyMintEvent.objects.count(), events)

    def test_a_mint_prices_nothing_differently(self):
        from toto.tariffs.rate_card import gas_asset

        assign_contract(node=node_id(), asset=self.asset)
        before = gas_asset()

        mint(asset=self.asset, amount=Decimal("400"), reason="more gas")

        self.assertEqual(gas_asset(), before)


class ABranchSeesNoMintLotsTests(TestCase):
    """The branch's side, simulated on this host: hash and balance only."""

    def setUp(self):
        super().setUp()
        self.reserve = _account("reserve")
        self.asset = create_currency(
            name="Assarion", unit_name="ASR", total_supply=Decimal("1000"),
            decimals=2, reserve_account=self.reserve, reference="open")
        self.descriptor = contracts.build_contract_descriptor(
            node=BRANCH, asset=self.asset, serial=1)
        self.descriptor["genesis_signature"] = self.asset.genesis_signature

    def test_a_mirror_carries_the_maximum_and_no_supply(self):
        # A branch importing a contract gets the standard, not the accounts.
        # It cannot even represent a mint lot: the chain has no mirror.
        issuer = self.asset.issuer
        genesis = self.descriptor["genesis"]

        self.assertIn("max_supply_base_units", genesis)
        self.assertNotIn("supply_base_units", genesis)
        self.assertNotIn("minted_base_units", genesis)

        # Mirroring is idempotent by hash, so this returns the same row here;
        # what matters is which fields it is willing to copy at all.
        mirrored = mirror_asset(genesis_payload=genesis,
                                signature=self.descriptor["genesis_signature"],
                                issuer=issuer)
        self.assertEqual(mirrored.max_supply_base_units,
                         genesis["max_supply_base_units"])

    @override_settings(CLEARING_SELF_PLATFORM_ID=BRANCH)
    def test_the_statement_a_branch_publishes_names_no_mint(self):
        from toto.assets.statement import build_statement

        assign_contract(node=BRANCH, asset=self.asset)
        mint_before = CurrencyMintEvent.objects.count()

        statement = build_statement()

        self.assertEqual(statement["currency_hash"], self.asset.currency_hash)
        for key in statement:
            self.assertNotIn("mint", key)
            self.assertNotIn("supply", key)
        self.assertEqual(CurrencyMintEvent.objects.count(), mint_before)
