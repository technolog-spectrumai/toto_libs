from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from .hashing import attach_hash, calculate_transaction_hash, verify_hash_chain
from .models import (
    Asset,
    AssetHolding,
    LedgerAccount,
    LedgerEntry,
    LedgerHash,
    LedgerTransaction,
    TransactionType,
    from_base_units,
    to_base_units,
)
from .queries import (
    get_asset_balance,
    get_asset_balance_display,
    get_asset_total_supply,
    get_transaction_by_reference,
    list_asset_holders,
    verify_asset_ledger,
)
from .services.assets import create_asset, reverse_transaction, transfer_asset


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_account(code, account_type="user", active=True):
    return LedgerAccount.objects.create(code=code, name=code, account_type=account_type, active=active)


def make_asset_direct(unit_name="TST", decimals=2, supply=10000):
    return Asset.objects.create(
        name="Test Asset",
        unit_name=unit_name,
        decimals=decimals,
        total_supply_base_units=supply,
        active=True,
    )


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

class AppSetupTests(TestCase):
    def test_models_importable(self):
        from toto.assets import models  # noqa: F401

    def test_services_importable(self):
        from toto.assets.services import assets  # noqa: F401

    def test_queries_importable(self):
        from toto.assets import queries  # noqa: F401


# ---------------------------------------------------------------------------
# Amount conversion
# ---------------------------------------------------------------------------

class AmountConversionTests(TestCase):
    def test_to_base_units_basic(self):
        self.assertEqual(to_base_units(Decimal("12.34"), 2), 1234)

    def test_to_base_units_zero_decimals(self):
        self.assertEqual(to_base_units(Decimal("100"), 0), 100)

    def test_to_base_units_large(self):
        self.assertEqual(to_base_units(Decimal("1000000.00"), 2), 100000000)

    def test_from_base_units_basic(self):
        self.assertEqual(from_base_units(1234, 2), Decimal("12.34"))

    def test_from_base_units_zero_decimals(self):
        self.assertEqual(from_base_units(100, 0), Decimal("100"))


# ---------------------------------------------------------------------------
# Asset creation
# ---------------------------------------------------------------------------

class AssetCreationTests(TestCase):
    def setUp(self):
        self.reserve = make_account("reserve", "reserve")

    def test_asset_created(self):
        asset = create_asset(
            name="Spectrum Credit",
            unit_name="SPC",
            total_supply=Decimal("1000000"),
            decimals=2,
            reserve_account=self.reserve,
            reference="create-spc-01",
        )
        self.assertIsNotNone(asset.pk)
        self.assertEqual(asset.unit_name, "SPC")
        self.assertEqual(asset.total_supply_base_units, 100000000)

    def test_reserve_receives_total_supply(self):
        asset = create_asset(
            name="Spectrum Credit",
            unit_name="SPC",
            total_supply=Decimal("1000"),
            decimals=2,
            reserve_account=self.reserve,
            reference="create-spc-02",
        )
        balance = get_asset_balance(asset, self.reserve)
        self.assertEqual(balance, 100000)

    def test_transaction_is_posted(self):
        create_asset(
            name="X", unit_name="X01", total_supply=Decimal("1"), decimals=0,
            reserve_account=self.reserve, reference="x01",
        )
        tx = LedgerTransaction.objects.get(reference="x01")
        self.assertTrue(tx.posted)

    def test_entries_are_balanced(self):
        asset = create_asset(
            name="X", unit_name="X02", total_supply=Decimal("500"), decimals=0,
            reserve_account=self.reserve, reference="x02",
        )
        total = sum(e.amount_base_units for e in LedgerEntry.objects.filter(asset=asset))
        self.assertEqual(total, 0)

    def test_hash_attached(self):
        create_asset(
            name="X", unit_name="X03", total_supply=Decimal("1"), decimals=0,
            reserve_account=self.reserve, reference="x03",
        )
        tx = LedgerTransaction.objects.get(reference="x03")
        self.assertTrue(hasattr(tx, "hash_record"))

    def test_invalid_supply_raises(self):
        with self.assertRaises(ValidationError):
            create_asset(
                name="X", unit_name="X04", total_supply=Decimal("0"), decimals=0,
                reserve_account=self.reserve, reference="x04",
            )

    def test_negative_supply_raises(self):
        with self.assertRaises(ValidationError):
            create_asset(
                name="X", unit_name="X05", total_supply=Decimal("-1"), decimals=0,
                reserve_account=self.reserve, reference="x05",
            )

    def test_invalid_decimals_raises(self):
        with self.assertRaises(ValidationError):
            create_asset(
                name="X", unit_name="X06", total_supply=Decimal("1"), decimals=20,
                reserve_account=self.reserve, reference="x06",
            )

    def test_duplicate_reference_raises(self):
        create_asset(
            name="X", unit_name="X07", total_supply=Decimal("1"), decimals=0,
            reserve_account=self.reserve, reference="dup-ref",
        )
        with self.assertRaises(Exception):
            create_asset(
                name="Y", unit_name="Y07", total_supply=Decimal("1"), decimals=0,
                reserve_account=self.reserve, reference="dup-ref",
            )


# ---------------------------------------------------------------------------
# Asset transfer
# ---------------------------------------------------------------------------

class AssetTransferTests(TestCase):
    def setUp(self):
        self.reserve = make_account("reserve", "reserve")
        self.alice = make_account("alice")
        self.bob = make_account("bob")
        self.asset = create_asset(
            name="Token", unit_name="TKN", total_supply=Decimal("1000"), decimals=2,
            reserve_account=self.reserve, reference="create-tkn",
        )

    def test_transfer_succeeds(self):
        tx = transfer_asset(
            asset=self.asset,
            sender_account=self.reserve,
            receiver_account=self.alice,
            amount=Decimal("100.00"),
            reference="txfr-01",
        )
        self.assertIsNotNone(tx.pk)
        self.assertTrue(tx.posted)

    def test_sender_balance_decreases(self):
        before = get_asset_balance(self.asset, self.reserve)
        transfer_asset(
            asset=self.asset, sender_account=self.reserve, receiver_account=self.alice,
            amount=Decimal("100.00"), reference="txfr-02",
        )
        after = get_asset_balance(self.asset, self.reserve)
        self.assertEqual(before - after, to_base_units(Decimal("100"), self.asset.decimals))

    def test_receiver_balance_increases(self):
        before = get_asset_balance(self.asset, self.alice)
        transfer_asset(
            asset=self.asset, sender_account=self.reserve, receiver_account=self.alice,
            amount=Decimal("100.00"), reference="txfr-03",
        )
        after = get_asset_balance(self.asset, self.alice)
        self.assertEqual(after - before, to_base_units(Decimal("100"), self.asset.decimals))

    def test_insufficient_balance_raises(self):
        with self.assertRaises(ValidationError):
            transfer_asset(
                asset=self.asset, sender_account=self.alice, receiver_account=self.bob,
                amount=Decimal("1.00"), reference="txfr-04",
            )

    def test_inactive_asset_raises(self):
        self.asset.active = False
        self.asset.save()
        with self.assertRaises(ValidationError):
            transfer_asset(
                asset=self.asset, sender_account=self.reserve, receiver_account=self.alice,
                amount=Decimal("1.00"), reference="txfr-05",
            )

    def test_inactive_sender_raises(self):
        inactive = make_account("inactive-sender", active=False)
        with self.assertRaises(ValidationError):
            transfer_asset(
                asset=self.asset, sender_account=inactive, receiver_account=self.alice,
                amount=Decimal("1.00"), reference="txfr-06",
            )

    def test_inactive_receiver_raises(self):
        inactive = make_account("inactive-receiver", active=False)
        with self.assertRaises(ValidationError):
            transfer_asset(
                asset=self.asset, sender_account=self.reserve, receiver_account=inactive,
                amount=Decimal("1.00"), reference="txfr-07",
            )

    def test_zero_amount_raises(self):
        with self.assertRaises(ValidationError):
            transfer_asset(
                asset=self.asset, sender_account=self.reserve, receiver_account=self.alice,
                amount=Decimal("0"), reference="txfr-08",
            )

    def test_negative_amount_raises(self):
        with self.assertRaises(ValidationError):
            transfer_asset(
                asset=self.asset, sender_account=self.reserve, receiver_account=self.alice,
                amount=Decimal("-1"), reference="txfr-09",
            )

    def test_duplicate_reference_raises(self):
        transfer_asset(
            asset=self.asset, sender_account=self.reserve, receiver_account=self.alice,
            amount=Decimal("1.00"), reference="dup-txfr",
        )
        with self.assertRaises(Exception):
            transfer_asset(
                asset=self.asset, sender_account=self.reserve, receiver_account=self.alice,
                amount=Decimal("1.00"), reference="dup-txfr",
            )

    def test_entries_balanced_after_transfer(self):
        transfer_asset(
            asset=self.asset, sender_account=self.reserve, receiver_account=self.alice,
            amount=Decimal("50.00"), reference="txfr-bal",
        )
        total = sum(
            e.amount_base_units
            for e in LedgerEntry.objects.filter(asset=self.asset)
        )
        self.assertEqual(total, 0)


# ---------------------------------------------------------------------------
# Reversal
# ---------------------------------------------------------------------------

class ReversalTests(TestCase):
    def setUp(self):
        self.reserve = make_account("reserve", "reserve")
        self.alice = make_account("alice")
        self.asset = create_asset(
            name="Token", unit_name="RVT", total_supply=Decimal("1000"), decimals=2,
            reserve_account=self.reserve, reference="create-rvt",
        )
        self.tx = transfer_asset(
            asset=self.asset, sender_account=self.reserve, receiver_account=self.alice,
            amount=Decimal("100.00"), reference="txfr-rvt-01",
        )

    def test_reversal_succeeds(self):
        rev = reverse_transaction(transaction=self.tx, reference="rev-txfr-rvt-01")
        self.assertIsNotNone(rev.pk)
        self.assertTrue(rev.posted)

    def test_original_transaction_unchanged(self):
        reverse_transaction(transaction=self.tx, reference="rev-txfr-rvt-02")
        self.tx.refresh_from_db()
        self.assertTrue(self.tx.posted)
        self.assertEqual(self.tx.transaction_type, TransactionType.ASSET_TRANSFER)

    def test_reversal_creates_opposite_entries(self):
        rev = reverse_transaction(transaction=self.tx, reference="rev-txfr-rvt-03")
        original_amounts = {
            e.account_id: e.amount_base_units
            for e in self.tx.entries.all()
        }
        for entry in rev.entries.all():
            self.assertEqual(entry.amount_base_units, -original_amounts[entry.account_id])

    def test_holdings_restored(self):
        reserve_before = get_asset_balance(self.asset, self.reserve)
        alice_before = get_asset_balance(self.asset, self.alice)

        reverse_transaction(transaction=self.tx, reference="rev-txfr-rvt-04")

        reserve_after = get_asset_balance(self.asset, self.reserve)
        alice_after = get_asset_balance(self.asset, self.alice)

        self.assertEqual(reserve_after, reserve_before + to_base_units(Decimal("100"), self.asset.decimals))
        self.assertEqual(alice_after, alice_before - to_base_units(Decimal("100"), self.asset.decimals))

    def test_cannot_reverse_twice(self):
        reverse_transaction(transaction=self.tx, reference="rev-txfr-rvt-05a")
        with self.assertRaises(ValidationError):
            reverse_transaction(transaction=self.tx, reference="rev-txfr-rvt-05b")

    def test_cannot_reverse_unposted(self):
        tx = LedgerTransaction.objects.create(
            reference="unposted-tx",
            transaction_type=TransactionType.ASSET_TRANSFER,
            asset=self.asset,
        )
        with self.assertRaises(ValidationError):
            reverse_transaction(transaction=tx, reference="rev-unposted")


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------

class ImmutabilityTests(TestCase):
    def setUp(self):
        self.reserve = make_account("reserve", "reserve")
        self.asset = create_asset(
            name="Token", unit_name="IMM", total_supply=Decimal("100"), decimals=0,
            reserve_account=self.reserve, reference="create-imm",
        )
        self.tx = LedgerTransaction.objects.get(reference="create-imm")

    def test_posted_transaction_cannot_be_edited(self):
        self.tx.description = "changed"
        with self.assertRaises(ValidationError):
            self.tx.save()

    def test_ledger_entry_cannot_be_edited(self):
        entry = LedgerEntry.objects.filter(transaction=self.tx).first()
        entry.amount_base_units = 999
        with self.assertRaises(ValidationError):
            entry.save()

    def test_ledger_entry_cannot_be_deleted(self):
        entry = LedgerEntry.objects.filter(transaction=self.tx).first()
        with self.assertRaises(ValidationError):
            entry.delete()


# ---------------------------------------------------------------------------
# Hash chain
# ---------------------------------------------------------------------------

class HashChainTests(TestCase):
    def setUp(self):
        self.reserve = make_account("reserve", "reserve")
        self.alice = make_account("alice")
        self.asset = create_asset(
            name="Token", unit_name="HCH", total_supply=Decimal("1000"), decimals=2,
            reserve_account=self.reserve, reference="create-hch",
        )
        self.tx = transfer_asset(
            asset=self.asset, sender_account=self.reserve, receiver_account=self.alice,
            amount=Decimal("50.00"), reference="txfr-hch-01",
        )

    def test_hash_chain_valid(self):
        self.assertTrue(verify_hash_chain())

    def test_tamper_hash_breaks_chain(self):
        record = LedgerHash.objects.last()
        LedgerHash.objects.filter(pk=record.pk).update(hash="deadbeef" * 8)
        self.assertFalse(verify_hash_chain())

    def test_tamper_previous_hash_breaks_chain(self):
        record = LedgerHash.objects.last()
        LedgerHash.objects.filter(pk=record.pk).update(previous_hash="tampered" * 8)
        self.assertFalse(verify_hash_chain())

    def test_reversal_hash_attached(self):
        rev = reverse_transaction(transaction=self.tx, reference="rev-hch-01")
        self.assertTrue(hasattr(rev, "hash_record"))

    def test_hash_chain_still_valid_after_reversal(self):
        reverse_transaction(transaction=self.tx, reference="rev-hch-02")
        self.assertTrue(verify_hash_chain())


# ---------------------------------------------------------------------------
# Supply verification
# ---------------------------------------------------------------------------

class SupplyTests(TestCase):
    def setUp(self):
        self.reserve = make_account("reserve", "reserve")
        self.alice = make_account("alice")
        self.bob = make_account("bob")
        self.asset = create_asset(
            name="Supply Test", unit_name="SUP", total_supply=Decimal("1000"), decimals=2,
            reserve_account=self.reserve, reference="create-sup",
        )

    def test_total_supply_helper(self):
        self.assertEqual(get_asset_total_supply(self.asset), 100000)

    def test_holdings_equal_total_supply_on_create(self):
        status = verify_asset_ledger(self.asset)
        self.assertTrue(status["total_supply_matches"])

    def test_holdings_equal_total_supply_after_transfer(self):
        transfer_asset(
            asset=self.asset, sender_account=self.reserve, receiver_account=self.alice,
            amount=Decimal("200.00"), reference="txfr-sup-01",
        )
        status = verify_asset_ledger(self.asset)
        self.assertTrue(status["total_supply_matches"])

    def test_entries_balanced_across_all_transactions(self):
        transfer_asset(
            asset=self.asset, sender_account=self.reserve, receiver_account=self.alice,
            amount=Decimal("100.00"), reference="txfr-sup-02",
        )
        tx = transfer_asset(
            asset=self.asset, sender_account=self.alice, receiver_account=self.bob,
            amount=Decimal("50.00"), reference="txfr-sup-03",
        )
        reverse_transaction(transaction=tx, reference="rev-sup-03")
        status = verify_asset_ledger(self.asset)
        self.assertTrue(status["entries_balanced"])

    def test_balance_display(self):
        balance = get_asset_balance_display(self.asset, self.reserve)
        self.assertEqual(balance, Decimal("1000.00"))

    def test_list_asset_holders(self):
        transfer_asset(
            asset=self.asset, sender_account=self.reserve, receiver_account=self.alice,
            amount=Decimal("100.00"), reference="txfr-sup-04",
        )
        holders = list_asset_holders(self.asset)
        codes = [h.account.code for h in holders]
        self.assertIn("reserve", codes)
        self.assertIn("alice", codes)

    def test_get_transaction_by_reference(self):
        tx = get_transaction_by_reference("create-sup")
        self.assertEqual(tx.reference, "create-sup")
