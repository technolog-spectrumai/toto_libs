"""MINT and BURN: the two verbs that move supply, and the two that do not.

The conservation table this file exists to prove:

    engrave     supply unchanged (zero)
    mint        supply up
    distribute  supply unchanged
    burn        supply down
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from toto.assets.models import (AccountType, LedgerAccount, LedgerTransaction,
                                TransactionType)
from toto.assets.queries import get_asset_balance
from toto.assets.services.assets import distribute_asset, engrave_currency
from toto.assets.testing import LedgerTestCase as TestCase
from toto.mint.history import maximum, supply, unminted, verify_chain
from toto.mint.models import CurrencyMintEvent
from toto.mint.services import burn, create_currency, mint


def _account(code, kind=AccountType.RESERVE):
    return LedgerAccount.objects.create(code=code, name=code,
                                        account_type=kind)


class SupplyTests(TestCase):
    def setUp(self):
        super().setUp()
        self.reserve = _account("reserve")
        self.asset = engrave_currency(name="Assarion", unit_name="ASR",
                                      max_supply=Decimal("1000"), decimals=2)
        self.asset.reserve_account = self.reserve
        self.asset.save(update_fields=["reserve_account", "updated_at"])

    def test_an_engraved_currency_starts_at_nothing(self):
        self.assertEqual(supply(self.asset), 0)
        self.assertEqual(maximum(self.asset), 100_000)
        self.assertEqual(unminted(self.asset), 100_000)

    def test_minting_credits_the_reserve_and_nothing_else(self):
        mint(asset=self.asset, amount=Decimal("100"), reason="opening")

        self.assertEqual(supply(self.asset), 10_000)
        self.assertEqual(unminted(self.asset), 90_000)
        self.assertEqual(get_asset_balance(self.asset, self.reserve), 10_000)

    def test_a_mint_posts_a_mint_transaction(self):
        event = mint(asset=self.asset, amount=Decimal("100"), reason="opening")

        tx = event.ledger_transaction
        self.assertEqual(tx.transaction_type, TransactionType.MINT)
        self.assertTrue(tx.posted)
        self.assertEqual(sum(e.amount_base_units for e in tx.entries.all()), 0)

    def test_successive_mints_accumulate(self):
        mint(asset=self.asset, amount=Decimal("100"), reason="first")
        mint(asset=self.asset, amount=Decimal("250"), reason="second")

        self.assertEqual(supply(self.asset), 35_000)
        self.assertEqual(get_asset_balance(self.asset, self.reserve), 35_000)
        self.assertTrue(verify_chain(), verify_chain().findings)

    def test_minting_past_the_maximum_is_refused(self):
        mint(asset=self.asset, amount=Decimal("900"), reason="most of it")

        with self.assertRaises(ValidationError) as caught:
            mint(asset=self.asset, amount=Decimal("200"), reason="too much")
        self.assertIn("maximum", str(caught.exception))
        self.assertEqual(supply(self.asset), 90_000)

    def test_minting_exactly_to_the_maximum_is_allowed(self):
        mint(asset=self.asset, amount=Decimal("1000"), reason="all of it")

        self.assertEqual(supply(self.asset), maximum(self.asset))
        self.assertEqual(unminted(self.asset), 0)

    def test_a_refused_mint_leaves_no_ledger_movement(self):
        with self.assertRaises(ValidationError):
            mint(asset=self.asset, amount=Decimal("2000"), reason="far too much")

        self.assertEqual(CurrencyMintEvent.objects.count(), 0)
        self.assertEqual(LedgerTransaction.objects.filter(
            asset=self.asset).count(), 0)
        self.assertEqual(get_asset_balance(self.asset, self.reserve), 0)

    def test_minting_needs_a_reason(self):
        with self.assertRaises(ValidationError):
            mint(asset=self.asset, amount=Decimal("1"), reason="  ")
        self.assertEqual(supply(self.asset), 0)

    def test_a_currency_with_no_reserve_cannot_be_minted(self):
        stray = engrave_currency(name="Stray", unit_name="STR",
                                 max_supply=Decimal("10"), decimals=0)
        with self.assertRaises(ValidationError) as caught:
            mint(asset=stray, amount=Decimal("1"), reason="nowhere to go")
        self.assertIn("reserve", str(caught.exception))

    def test_the_amount_is_said_once_and_only_once(self):
        # Base units and display amounts are exactly the mix-up decimals exist
        # to prevent, so supplying both, or neither, is a caller bug.
        with self.assertRaises(ValidationError):
            mint(asset=self.asset, amount=Decimal("1"),
                 amount_base_units=100, reason="both")
        with self.assertRaises(ValidationError):
            mint(asset=self.asset, reason="neither")


class BurnTests(TestCase):
    def setUp(self):
        super().setUp()
        self.reserve = _account("reserve")
        self.asset = create_currency(
            name="Assarion", unit_name="ASR", total_supply=Decimal("1000"),
            decimals=2, reserve_account=self.reserve, reference="open")

    def test_burning_destroys_units_and_lowers_supply(self):
        burn(asset=self.asset, amount=Decimal("400"), reason="withdrawn")

        self.assertEqual(supply(self.asset), 60_000)
        self.assertEqual(get_asset_balance(self.asset, self.reserve), 60_000)

    def test_burning_leaves_room_to_mint_again(self):
        # The ceiling caps what EXISTS, not what has ever existed — otherwise
        # burning would quietly retire part of the currency forever.
        burn(asset=self.asset, amount=Decimal("400"), reason="withdrawn")
        self.assertEqual(unminted(self.asset), 40_000)

        mint(asset=self.asset, amount=Decimal("400"), reason="back again")
        self.assertEqual(supply(self.asset), 100_000)

    def test_a_burn_posts_a_burn_transaction(self):
        event = burn(asset=self.asset, amount=Decimal("10"), reason="dust")

        self.assertEqual(event.ledger_transaction.transaction_type,
                         TransactionType.BURN)
        self.assertEqual(event.kind, "burn")
        self.assertEqual(event.amount_base_units, 1_000)

    def test_only_units_nobody_holds_can_be_burned(self):
        holder = _account("holder", AccountType.USER)
        distribute_asset(asset=self.asset, recipient_account=holder,
                         amount=Decimal("900"), reference="give")

        with self.assertRaises(ValidationError) as caught:
            burn(asset=self.asset, amount=Decimal("500"), reason="claw back")
        self.assertIn("reserve holds", str(caught.exception))
        self.assertEqual(get_asset_balance(self.asset, holder), 90_000)
        self.assertEqual(supply(self.asset), 100_000)

    def test_burning_the_whole_reserve_is_allowed(self):
        burn(asset=self.asset, amount=Decimal("1000"), reason="all of it")

        self.assertEqual(supply(self.asset), 0)
        self.assertEqual(get_asset_balance(self.asset, self.reserve), 0)
        self.assertTrue(verify_chain(), verify_chain().findings)

    def test_a_refused_burn_leaves_no_ledger_movement(self):
        before = CurrencyMintEvent.objects.count()
        with self.assertRaises(ValidationError):
            burn(asset=self.asset, amount=Decimal("5000"), reason="impossible")

        self.assertEqual(CurrencyMintEvent.objects.count(), before)
        self.assertEqual(get_asset_balance(self.asset, self.reserve), 100_000)


class ConservationTests(TestCase):
    """One test per verb, all four in the same units."""

    def setUp(self):
        super().setUp()
        self.reserve = _account("reserve")
        self.holder = _account("holder", AccountType.USER)

    def test_engrave_creates_no_supply(self):
        asset = engrave_currency(name="Assarion", unit_name="ASR",
                                 max_supply=Decimal("1000"), decimals=2)
        self.assertEqual(supply(asset), 0)

    def test_mint_raises_supply(self):
        asset = create_currency(
            name="Assarion", unit_name="ASR", total_supply=Decimal("1000"),
            decimals=2, reserve_account=self.reserve, reference="open")
        self.assertEqual(supply(asset), 100_000)

    def test_distribute_changes_supply_not_at_all(self):
        # The distinction the whole design rests on: moving money is not
        # making money, however much more of it a recipient can suddenly see.
        asset = create_currency(
            name="Assarion", unit_name="ASR", total_supply=Decimal("1000"),
            decimals=2, reserve_account=self.reserve, reference="open")
        before = supply(asset)

        distribute_asset(asset=asset, recipient_account=self.holder,
                         amount=Decimal("300"), reference="give")

        self.assertEqual(supply(asset), before)
        self.assertEqual(get_asset_balance(asset, self.holder), 30_000)
        self.assertEqual(
            CurrencyMintEvent.objects.filter(
                currency_hash=asset.currency_hash).count(), 1)

    def test_burn_lowers_supply(self):
        asset = create_currency(
            name="Assarion", unit_name="ASR", total_supply=Decimal("1000"),
            decimals=2, reserve_account=self.reserve, reference="open")

        burn(asset=asset, amount=Decimal("250"), reason="withdrawn")
        self.assertEqual(supply(asset), 75_000)

    def test_the_chain_stays_verifiable_through_all_four(self):
        asset = create_currency(
            name="Assarion", unit_name="ASR", total_supply=Decimal("400"),
            decimals=2, reserve_account=self.reserve, reference="open")
        distribute_asset(asset=asset, recipient_account=self.holder,
                         amount=Decimal("100"), reference="give")
        burn(asset=asset, amount=Decimal("100"), reason="withdrawn")

        verdict = verify_chain()
        self.assertTrue(verdict, verdict.findings)
        self.assertEqual(supply(asset), 30_000)

    def test_supply_is_per_currency_and_the_chain_is_not(self):
        one = create_currency(
            name="Assarion", unit_name="ASR", total_supply=Decimal("100"),
            decimals=2, reserve_account=self.reserve, reference="one")
        other_reserve = _account("reserve-2")
        two = create_currency(
            name="Florin", unit_name="FLOR", total_supply=Decimal("500"),
            decimals=2, reserve_account=other_reserve, reference="two")

        self.assertEqual(supply(one), 10_000)
        self.assertEqual(supply(two), 50_000)
        self.assertEqual(CurrencyMintEvent.objects.count(), 2)


class BranchCannotMoveSupplyTests(TestCase):
    def setUp(self):
        super().setUp()
        self.reserve = _account("reserve")
        self.asset = create_currency(
            name="Assarion", unit_name="ASR", total_supply=Decimal("1000"),
            decimals=2, reserve_account=self.reserve, reference="open")
        from toto.assets.models import CurrencyIssuer

        CurrencyIssuer.objects.filter(is_self=True).update(
            private_key_encrypted=None)

    def test_a_branch_cannot_mint(self):
        from toto.assets.issuer import NotTheMaster

        with self.assertRaises(NotTheMaster):
            mint(asset=self.asset, amount=Decimal("1"), reason="forge")
        self.assertEqual(supply(self.asset), 100_000)

    def test_a_branch_cannot_burn(self):
        from toto.assets.issuer import NotTheMaster

        with self.assertRaises(NotTheMaster):
            burn(asset=self.asset, amount=Decimal("1"), reason="forge")
        self.assertEqual(supply(self.asset), 100_000)
