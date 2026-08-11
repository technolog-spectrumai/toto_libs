"""The mint creates new assets. It can never inflate an existing one."""

from decimal import Decimal

from django.core.exceptions import ValidationError

from toto.assets.models import Asset, CurrencyIssuer, TransactionType
from toto.assets.testing import LedgerTestCase as TestCase
from toto.mint.models import IssuanceRecord
from toto.mint.services import issue_asset


class IssueTests(TestCase):
    def test_issuing_creates_a_signed_asset_and_a_record(self):
        asset = issue_asset(name="Assarion", unit_name="ASR",
                            total_supply=Decimal("100"), decimals=9,
                            reason="Platform gas.")

        self.assertTrue(asset.verify_genesis())
        record = IssuanceRecord.objects.get()
        self.assertEqual(record.asset, asset)
        self.assertEqual(record.unit_name, "ASR")
        self.assertEqual(record.reason, "Platform gas.")
        self.assertEqual(record.currency_hash, asset.currency_hash)

    def test_the_whole_supply_lands_in_the_reserve(self):
        asset = issue_asset(name="Assarion", unit_name="ASR",
                            total_supply=Decimal("100"), decimals=2,
                            reason="Gas.")

        holding = asset.holdings.get(account=asset.reserve_account)
        self.assertEqual(holding.balance_base_units,
                         asset.max_supply_base_units)

    def test_the_opening_mint_is_linked(self):
        # The posting an issuance record points at is the MINT that filled the
        # reserve. Engraving posts nothing, because it creates nothing.
        issue_asset(name="A", unit_name="AAA", total_supply=Decimal("1"),
                    decimals=0, reason="Because.")
        record = IssuanceRecord.objects.get()
        self.assertEqual(record.ledger_transaction.transaction_type,
                         TransactionType.MINT)

    def test_a_reason_is_required(self):
        # It is the only account of why this exists, and it cannot be added
        # afterwards — the record is append-only.
        with self.assertRaises(ValidationError) as caught:
            issue_asset(name="A", unit_name="AAA", total_supply=Decimal("1"),
                        decimals=0, reason="   ")
        self.assertIn("needs a reason", "; ".join(caught.exception.messages))

    def test_a_refused_issuance_writes_nothing(self):
        before = Asset.objects.count()
        with self.assertRaises(ValidationError):
            issue_asset(name="A", unit_name="AAA", total_supply=Decimal("1"),
                        decimals=0, reason="")
        self.assertEqual(Asset.objects.count(), before)
        self.assertEqual(IssuanceRecord.objects.count(), 0)


class NoSecretInflationTests(TestCase):
    """The invariant that makes the name safe.

    Supply is not fixed — it is CAPPED, and every change to it is a signed
    event anyone can read. What remains forbidden is a change nobody can see:
    an edited column, a raised ceiling, a mint on a branch.
    """

    def setUp(self):
        self.asset = issue_asset(name="Assarion", unit_name="ASR",
                                 total_supply=Decimal("100"), decimals=9,
                                 reason="Gas.")

    def test_minting_is_a_transaction_type_of_its_own(self):
        # Distinct from ASSET_TRANSFER on purpose: a transfer moves units that
        # already exist. Conflating them would make "how much of this is
        # there?" a question the ledger could answer two different ways.
        kinds = {choice[0] for choice in TransactionType.choices}
        self.assertIn("mint", kinds)
        self.assertIn("burn", kinds)
        self.assertIn("asset_transfer", kinds)

    def test_every_unit_in_existence_came_from_a_signed_event(self):
        from toto.mint.history import supply, verify_chain
        from toto.mint.models import CurrencyMintEvent

        self.assertEqual(supply(self.asset), 100 * 10 ** 9)
        self.assertEqual(
            CurrencyMintEvent.objects.filter(
                currency_hash=self.asset.currency_hash).count(), 1)
        self.assertTrue(verify_chain())

    def test_the_maximum_cannot_be_raised_afterwards(self):
        # The ceiling is in the currency hash. Moving it would make the
        # identity describe a promise that was never made.
        self.asset.max_supply_base_units *= 2
        with self.assertRaises(ValidationError):
            self.asset.save()

    def test_minting_past_the_maximum_is_refused(self):
        from toto.mint.services import mint

        with self.assertRaises(ValidationError) as caught:
            mint(asset=self.asset, amount=Decimal("1"),
                 reason="One more, quietly.")
        self.assertIn("maximum", str(caught.exception))

    def test_issuing_the_same_ticker_again_is_a_different_asset(self):
        # Re-issuing is honest; inflating quietly is not. A second ASR is a
        # second identity, not more of the first.
        again = issue_asset(name="Assarion", unit_name="ASR2",
                            total_supply=Decimal("100"), decimals=9,
                            reason="Another.")
        self.assertNotEqual(again.currency_hash, self.asset.currency_hash)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.max_supply_display, Decimal("100"))

    def test_the_record_cannot_be_edited_or_deleted(self):
        record = IssuanceRecord.objects.get()
        record.reason = "something else"
        with self.assertRaises(ValidationError):
            record.save()
        with self.assertRaises(ValidationError):
            record.delete()


class BranchCannotMintTests(TestCase):
    def test_without_an_issuer_key_the_mint_refuses(self):
        from toto.assets.issuer import NotTheMaster

        CurrencyIssuer.objects.filter(is_self=True).update(
            private_key_encrypted=None)

        with self.assertRaises(NotTheMaster):
            issue_asset(name="Forged", unit_name="FRG",
                        total_supply=Decimal("1"), decimals=0,
                        reason="Should never happen.")
