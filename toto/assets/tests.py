from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

_SIMPLE_STATIC = "django.contrib.staticfiles.storage.StaticFilesStorage"

from .hashing import attach_hash, calculate_transaction_hash, verify_hash_chain
from .models import (
    Asset,
    LedgerAccount,
    LedgerEntry,
    LedgerHash,
    LedgerTransaction,
    Tokenization,
    TokenizationDefaultReason,
    TokenizationStatus,
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
from .services.assets import create_asset, default_tokenization, reverse_transaction, transfer_asset


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_account(code, account_type="user", active=True):
    return LedgerAccount.objects.create(code=code, name=code, account_type=account_type, active=active)


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
# Tokenization default
# ---------------------------------------------------------------------------

class TokenizationDefaultTests(TestCase):
    def setUp(self):
        from toto.inventory.models import RealWorldObject

        self.reserve = make_account("reserve", "reserve")
        self.asset = create_asset(
            name="Tokenized Object",
            unit_name="TOKOBJ",
            total_supply=Decimal("1"),
            decimals=0,
            reserve_account=self.reserve,
            reference="create-tokobj",
        )
        self.obj = RealWorldObject.objects.create(name="Broken machine")
        self.tokenization = Tokenization.objects.create(
            real_world_object=self.obj,
            asset=self.asset,
        )

    def test_default_tokenization_marks_asset_inactive(self):
        defaulted = default_tokenization(
            tokenization=self.tokenization,
            reason=TokenizationDefaultReason.BROKEN,
            note="Main bearing cracked.",
        )

        self.asset.refresh_from_db()
        self.assertEqual(defaulted.status, TokenizationStatus.DEFAULTED)
        self.assertEqual(defaulted.default_reason, TokenizationDefaultReason.BROKEN)
        self.assertFalse(self.asset.active)
        self.assertEqual(self.asset.metadata["tokenization_default"]["reason"], TokenizationDefaultReason.BROKEN)

    def test_default_tokenization_cannot_run_twice(self):
        default_tokenization(
            tokenization=self.tokenization,
            reason=TokenizationDefaultReason.NO_LONGER_EXISTS,
        )

        with self.assertRaises(ValidationError):
            default_tokenization(
                tokenization=self.tokenization,
                reason=TokenizationDefaultReason.BROKEN,
            )


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


# ---------------------------------------------------------------------------
# Lapis loader / compiler / executor
# ---------------------------------------------------------------------------

class LapisLoaderTests(TestCase):
    def test_json_roundtrip(self):
        from .lapis.loader import loads_contract, dumps_contract
        tree = {"language": "lapis", "version": 1, "name": "T", "actions": {"noop": {"type": "seq", "steps": []}}}
        text = dumps_contract(tree, fmt="json")
        self.assertEqual(loads_contract(text, fmt="json")["language"], "lapis")

    def test_yaml_roundtrip(self):
        from .lapis.loader import loads_contract, dumps_contract
        tree = {"language": "lapis", "version": 1, "name": "T", "actions": {"noop": {"type": "seq", "steps": []}}}
        text = dumps_contract(tree, fmt="yaml")
        self.assertEqual(loads_contract(text, fmt="yaml")["language"], "lapis")

    def test_detect_json_from_brace(self):
        from .lapis.loader import detect_format
        self.assertEqual(detect_format('{"key": 1}'), "json")

    def test_detect_yaml_from_dashes(self):
        from .lapis.loader import detect_format
        self.assertEqual(detect_format("---\nkey: val"), "yaml")

    def test_invalid_json_raises(self):
        from .lapis.loader import loads_contract
        from .lapis.exceptions import LapisValidationError
        with self.assertRaises(LapisValidationError):
            loads_contract("{bad json}", fmt="json")

    def test_non_dict_raises(self):
        from .lapis.loader import loads_contract
        from .lapis.exceptions import LapisValidationError
        with self.assertRaises(LapisValidationError):
            loads_contract("[1, 2, 3]", fmt="json")


class LapisCompilerTests(TestCase):
    def _valid_tree(self):
        return {
            "language": "lapis", "version": 1, "target": "teal", "name": "T",
            "actions": {"noop": {"body": {"type": "seq", "steps": []}}},
        }

    def test_valid_contract_passes(self):
        from .lapis.compiler import LapisCompiler
        LapisCompiler().validate_contract(self._valid_tree())

    def test_missing_language_raises(self):
        from .lapis.compiler import LapisCompiler
        from .lapis.exceptions import LapisValidationError
        tree = self._valid_tree()
        del tree["language"]
        with self.assertRaises(LapisValidationError):
            LapisCompiler().validate_contract(tree)

    def test_wrong_version_raises(self):
        from .lapis.compiler import LapisCompiler
        from .lapis.exceptions import LapisValidationError
        tree = {**self._valid_tree(), "version": 99}
        with self.assertRaises(LapisValidationError):
            LapisCompiler().validate_contract(tree)

    def test_missing_target_raises(self):
        from .lapis.compiler import LapisCompiler
        from .lapis.exceptions import LapisValidationError
        tree = self._valid_tree()
        del tree["target"]
        with self.assertRaises(LapisValidationError):
            LapisCompiler().validate_contract(tree)

    def test_wrong_target_raises(self):
        from .lapis.compiler import LapisCompiler
        from .lapis.exceptions import LapisValidationError
        tree = {**self._valid_tree(), "target": "evm"}
        with self.assertRaises(LapisValidationError):
            LapisCompiler().validate_contract(tree)

    def test_action_missing_body_raises(self):
        from .lapis.compiler import LapisCompiler
        from .lapis.exceptions import LapisValidationError
        tree = {**self._valid_tree(), "actions": {"a": {"type": "seq", "steps": []}}}
        with self.assertRaises(LapisValidationError):
            LapisCompiler().validate_contract(tree)

    def test_empty_actions_raises(self):
        from .lapis.compiler import LapisCompiler
        from .lapis.exceptions import LapisValidationError
        tree = {**self._valid_tree(), "actions": {}}
        with self.assertRaises(LapisValidationError):
            LapisCompiler().validate_contract(tree)

    def test_unknown_node_type_raises(self):
        from .lapis.compiler import LapisCompiler
        from .lapis.exceptions import LapisValidationError
        tree = {**self._valid_tree(), "actions": {"a": {"body": {"type": "BOGUS"}}}}
        with self.assertRaises(LapisValidationError):
            LapisCompiler().validate_contract(tree)

    def test_removed_transfer_node_raises(self):
        from .lapis.compiler import LapisCompiler
        from .lapis.exceptions import LapisValidationError
        tree = {**self._valid_tree(), "actions": {"a": {"body": {"type": "transfer"}}}}
        with self.assertRaises(LapisValidationError):
            LapisCompiler().validate_contract(tree)

    def test_removed_record_node_raises(self):
        from .lapis.compiler import LapisCompiler
        from .lapis.exceptions import LapisValidationError
        tree = {**self._valid_tree(), "actions": {"a": {"body": {"type": "record", "kind": "x"}}}}
        with self.assertRaises(LapisValidationError):
            LapisCompiler().validate_contract(tree)

    def test_removed_decimal_node_raises(self):
        from .lapis.compiler import LapisCompiler
        from .lapis.exceptions import LapisValidationError
        tree = {**self._valid_tree(), "actions": {"a": {"body": {"type": "decimal", "value": "1.5"}}}}
        with self.assertRaises(LapisValidationError):
            LapisCompiler().validate_contract(tree)

    def test_removed_get_state_node_raises(self):
        from .lapis.compiler import LapisCompiler
        from .lapis.exceptions import LapisValidationError
        tree = {**self._valid_tree(), "actions": {"a": {"body": {"type": "get_state", "key": "x"}}}}
        with self.assertRaises(LapisValidationError):
            LapisCompiler().validate_contract(tree)

    def test_removed_int_node_raises(self):
        from .lapis.compiler import LapisCompiler
        from .lapis.exceptions import LapisValidationError
        tree = {**self._valid_tree(), "actions": {"a": {"body": {"type": "int", "value": 1}}}}
        with self.assertRaises(LapisValidationError):
            LapisCompiler().validate_contract(tree)

    def test_compile_action_returns_body(self):
        from .lapis.compiler import LapisCompiler
        tree = self._valid_tree()
        node = LapisCompiler().compile_action(tree, "noop")
        self.assertEqual(node["type"], "seq")

    def test_compile_unknown_action_raises(self):
        from .lapis.compiler import LapisCompiler
        from .lapis.exceptions import LapisValidationError
        with self.assertRaises(LapisValidationError):
            LapisCompiler().compile_action(self._valid_tree(), "nonexistent")

    def test_app_global_get_validates(self):
        from .lapis.compiler import LapisCompiler
        node = {"type": "app_global_get", "key": {"type": "bytes", "value": "k"}}
        LapisCompiler().validate_node(node)

    def test_app_global_put_validates(self):
        from .lapis.compiler import LapisCompiler
        node = {
            "type": "app_global_put",
            "key": {"type": "bytes", "value": "k"},
            "value": {"type": "uint64", "value": 1},
        }
        LapisCompiler().validate_node(node)

    def test_inner_transaction_sequence_validates(self):
        from .lapis.compiler import LapisCompiler
        seq = {
            "type": "seq",
            "steps": [
                {"type": "inner_transaction_begin"},
                {"type": "inner_transaction_set", "field": "TypeEnum",
                 "value": {"type": "uint64", "value": 1}},
                {"type": "inner_transaction_submit"},
            ],
        }
        LapisCompiler().validate_node(seq)


class LapisExecutorTests(TestCase):
    def _ctx(self, global_state=None):
        from .lapis.executor import LapisContext
        return LapisContext(
            global_state=global_state or {},
            local_state={},
            boxes={},
            transaction={},
            group_transactions=[],
            global_fields={},
            app_args=[],
        )

    def test_execute_seq_noop(self):
        from .lapis.compiler import LapisCompiler
        from .lapis.executor import LapisExecutor
        tree = {
            "language": "lapis", "version": 1, "target": "teal", "name": "T",
            "actions": {"noop": {"body": {"type": "seq", "steps": []}}},
        }
        plan = LapisCompiler().compile_action(tree, "noop")
        LapisExecutor().execute(plan, self._ctx())

    def test_uint64_literal_eval(self):
        from .lapis.executor import LapisExecutor
        node = {"type": "uint64", "value": 42}
        self.assertEqual(LapisExecutor().eval_node(node, self._ctx()), 42)

    def test_bytes_literal_eval(self):
        from .lapis.executor import LapisExecutor
        node = {"type": "bytes", "value": "hello"}
        self.assertEqual(LapisExecutor().eval_node(node, self._ctx()), "hello")

    def test_add_node(self):
        from .lapis.executor import LapisExecutor
        node = {
            "type": "add",
            "left": {"type": "uint64", "value": 3},
            "right": {"type": "uint64", "value": 7},
        }
        self.assertEqual(LapisExecutor().eval_node(node, self._ctx()), 10)

    def test_mod_node(self):
        from .lapis.executor import LapisExecutor
        node = {
            "type": "mod",
            "left": {"type": "uint64", "value": 10},
            "right": {"type": "uint64", "value": 3},
        }
        self.assertEqual(LapisExecutor().eval_node(node, self._ctx()), 1)

    def test_eq_node_true(self):
        from .lapis.executor import LapisExecutor
        node = {
            "type": "eq",
            "left": {"type": "uint64", "value": 5},
            "right": {"type": "uint64", "value": 5},
        }
        self.assertTrue(LapisExecutor().eval_node(node, self._ctx()))

    def test_eq_node_false(self):
        from .lapis.executor import LapisExecutor
        node = {
            "type": "eq",
            "left": {"type": "uint64", "value": 5},
            "right": {"type": "uint64", "value": 6},
        }
        self.assertFalse(LapisExecutor().eval_node(node, self._ctx()))

    def test_app_global_put_and_get(self):
        from .lapis.executor import LapisExecutor
        ctx = self._ctx()
        put = {"type": "app_global_put", "key": {"type": "bytes", "value": "x"}, "value": {"type": "uint64", "value": 99}}
        LapisExecutor().eval_node(put, ctx)
        self.assertEqual(ctx.global_state["x"], 99)
        get = {"type": "app_global_get", "key": {"type": "bytes", "value": "x"}}
        self.assertEqual(LapisExecutor().eval_node(get, ctx), 99)

    def test_log_appends_to_logs(self):
        from .lapis.executor import LapisExecutor
        ctx = self._ctx()
        node = {"type": "log", "value": {"type": "bytes", "value": "hello"}}
        LapisExecutor().eval_node(node, ctx)
        self.assertEqual(ctx.logs, ["hello"])

    def test_approve_sets_result(self):
        from .lapis.executor import LapisExecutor
        ctx = self._ctx()
        LapisExecutor().eval_node({"type": "approve"}, ctx)
        self.assertEqual(ctx.result, "approve")

    def test_reject_sets_result(self):
        from .lapis.executor import LapisExecutor
        ctx = self._ctx()
        LapisExecutor().eval_node({"type": "reject"}, ctx)
        self.assertEqual(ctx.result, "reject")

    def test_inner_transaction_sequence(self):
        from .lapis.executor import LapisExecutor
        ctx = self._ctx()
        seq = {
            "type": "seq",
            "steps": [
                {"type": "inner_transaction_begin"},
                {"type": "inner_transaction_set", "field": "TypeEnum", "value": {"type": "uint64", "value": 1}},
                {"type": "inner_transaction_set", "field": "Amount", "value": {"type": "uint64", "value": 500}},
                {"type": "inner_transaction_submit"},
            ],
        }
        LapisExecutor().eval_node(seq, ctx)
        self.assertEqual(len(ctx.inner_transactions), 1)
        self.assertEqual(ctx.inner_transactions[0]["TypeEnum"], 1)
        self.assertEqual(ctx.inner_transactions[0]["Amount"], 500)

    def test_sha256_node(self):
        import hashlib
        from .lapis.executor import LapisExecutor
        ctx = self._ctx()
        node = {"type": "sha256", "value": {"type": "bytes", "value": "abc"}}
        result = LapisExecutor().eval_node(node, ctx)
        self.assertEqual(result, hashlib.sha256(b"abc").hexdigest())

    def test_concat_node(self):
        from .lapis.executor import LapisExecutor
        ctx = self._ctx()
        node = {
            "type": "concat",
            "left": {"type": "bytes", "value": "foo"},
            "right": {"type": "bytes", "value": "bar"},
        }
        self.assertEqual(LapisExecutor().eval_node(node, ctx), "foobar")


# ---------------------------------------------------------------------------
# Shared Lapis fixtures
# ---------------------------------------------------------------------------

_SIMPLE_LAPIS_YAML = """\
language: lapis
version: 1
target: teal
contract_type: application
name: T
actions:
  noop:
    body:
      type: seq
      steps: []
"""

_SUBSCRIPTION_LAPIS_YAML = """\
language: lapis
version: 1
target: teal
contract_type: application
name: Subscription
actions:
  activate:
    body:
      type: seq
      steps:
        - type: app_global_put
          key:
            type: bytes
            value: "status"
          value:
            type: bytes
            value: "active"
        - type: log
          value:
            type: bytes
            value: "subscription_activated"
        - type: approve
  bill_period:
    body:
      type: seq
      steps:
        - type: assert
          condition:
            type: eq
            left:
              type: app_global_get
              key:
                type: bytes
                value: "status"
            right:
              type: bytes
              value: "active"
        - type: inner_transaction_begin
        - type: inner_transaction_set
          field: "TypeEnum"
          value:
            type: uint64
            value: 1
        - type: inner_transaction_set
          field: "Amount"
          value:
            type: uint64
            value: 1000
        - type: inner_transaction_submit
        - type: log
          value:
            type: bytes
            value: "period_billed"
        - type: approve
  cancel:
    body:
      type: seq
      steps:
        - type: app_global_put
          key:
            type: bytes
            value: "status"
          value:
            type: bytes
            value: "cancelled"
        - type: log
          value:
            type: bytes
            value: "subscription_cancelled"
        - type: approve
"""


# ---------------------------------------------------------------------------
# Contract model
# ---------------------------------------------------------------------------

class ContractTests(TestCase):
    def test_uuid_generated_on_agreement(self):
        from .models import Agreement
        src = make_account("ct-src", "user")
        tgt = make_account("ct-tgt", "user")
        agr = Agreement.objects.create(source_account=src, target_account=tgt)
        self.assertIsNotNone(agr.uuid)

    def test_create_contract(self):
        from .models import Contract
        c = Contract.objects.create(name="Basic", code=_SIMPLE_LAPIS_YAML)
        self.assertIsNotNone(c.pk)

    def test_blank_code_is_valid(self):
        from .models import Contract
        Contract(name="Empty").full_clean()

    def test_valid_lapis_passes(self):
        from .models import Contract
        Contract(name="Valid", code=_SIMPLE_LAPIS_YAML).full_clean()

    def test_malformed_yaml_fails(self):
        from django.core.exceptions import ValidationError
        from .models import Contract
        with self.assertRaises(ValidationError):
            Contract(name="Bad", code=": {{{{").full_clean()

    def test_unsupported_node_type_fails(self):
        from django.core.exceptions import ValidationError
        from .models import Contract
        code = (
            "language: lapis\nversion: 1\ntarget: teal\nname: X\n"
            "actions:\n  a:\n    body:\n      type: BOGUS\n"
        )
        with self.assertRaises(ValidationError):
            Contract(name="BadNode", code=code).full_clean()

    def test_old_transfer_node_rejected(self):
        from django.core.exceptions import ValidationError
        from .models import Contract
        code = (
            "language: lapis\nversion: 1\ntarget: teal\nname: X\n"
            "actions:\n  a:\n    body:\n      type: transfer\n"
        )
        with self.assertRaises(ValidationError):
            Contract(name="OldTransfer", code=code).full_clean()

    def test_old_record_node_rejected(self):
        from django.core.exceptions import ValidationError
        from .models import Contract
        code = (
            "language: lapis\nversion: 1\ntarget: teal\nname: X\n"
            "actions:\n  a:\n    body:\n      type: record\n      kind: x\n"
        )
        with self.assertRaises(ValidationError):
            Contract(name="OldRecord", code=code).full_clean()

    def test_missing_target_fails(self):
        from django.core.exceptions import ValidationError
        from .models import Contract
        code = "language: lapis\nversion: 1\nname: X\nactions:\n  a:\n    body:\n      type: seq\n      steps: []\n"
        with self.assertRaises(ValidationError):
            Contract(name="NoTarget", code=code).full_clean()

    def test_action_names_extractable(self):
        from .lapis.loader import loads_contract
        from .lapis.compiler import LapisCompiler
        tree = loads_contract(_SUBSCRIPTION_LAPIS_YAML, fmt="yaml")
        LapisCompiler().validate_contract(tree)
        actions = list(tree["actions"].keys())
        self.assertEqual(actions, ["activate", "bill_period", "cancel"])

    def test_source_target_must_differ(self):
        from django.core.exceptions import ValidationError
        from .models import Agreement
        acc = make_account("same-acc", "user")
        with self.assertRaises(ValidationError):
            Agreement(source_account=acc, target_account=acc).full_clean()

    def test_str_is_name(self):
        from .models import Contract
        self.assertEqual(str(Contract(name="MyContract")), "MyContract")


# ---------------------------------------------------------------------------
# Agreement form tests
# ---------------------------------------------------------------------------

class AgreementFormTests(TestCase):
    def setUp(self):
        self.source = make_account("form-src", "user")
        self.target = make_account("form-tgt", "user")

    def _post(self, extra=None):
        from .forms import AgreementForm
        data = {
            "source_account": self.source.pk,
            "target_account": self.target.pk,
            "metadata": "{}",
        }
        if extra:
            data.update(extra)
        return AgreementForm(data)

    def test_valid_form_no_code(self):
        form = self._post()
        self.assertTrue(form.is_valid(), form.errors)

    def test_valid_form_with_code(self):
        form = self._post({"code": _SUBSCRIPTION_LAPIS_YAML})
        self.assertTrue(form.is_valid(), form.errors)

    def test_same_source_target_rejected(self):
        from .forms import AgreementForm
        form = AgreementForm({
            "source_account": self.source.pk,
            "target_account": self.source.pk,
            "metadata": "{}",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("__all__", form.errors)

    def test_malformed_yaml_rejected(self):
        form = self._post({"code": ": {{{{ bad"})
        self.assertFalse(form.is_valid())
        self.assertIn("code", form.errors)

    def test_invalid_lapis_rejected(self):
        bad_code = (
            "language: lapis\nversion: 1\ntarget: teal\nname: X\n"
            "actions:\n  a:\n    body:\n      type: BOGUS\n"
        )
        form = self._post({"code": bad_code})
        self.assertFalse(form.is_valid())
        self.assertIn("code", form.errors)


# ---------------------------------------------------------------------------
# Agreement view tests
# ---------------------------------------------------------------------------

def _make_platform():
    from toto.core.models import Platform
    Platform.objects.get_or_create(
        site_name="Test",
        defaults={"author": "test", "publication_year": 2024, "active": True},
    )


@override_settings(STATICFILES_STORAGE=_SIMPLE_STATIC)
class AgreementViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        _make_platform()
        User = get_user_model()
        self.user = User.objects.create_user(username="testuser", password="testpass")
        self.source = make_account("view-src", "user")
        self.target = make_account("view-tgt", "user")

    def _login(self):
        self.client.force_login(self.user)

    def test_list_requires_login(self):
        from django.urls import reverse
        resp = self.client.get(reverse("assets:agreement_list"))
        self.assertNotEqual(resp.status_code, 200)

    def test_create_requires_login(self):
        from django.urls import reverse
        resp = self.client.get(reverse("assets:agreement_create"))
        self.assertNotEqual(resp.status_code, 200)

    def test_authenticated_user_can_create_with_valid_yaml(self):
        from django.urls import reverse
        from .models import Agreement, Contract
        self._login()
        resp = self.client.post(reverse("assets:agreement_create"), {
            "source_account": self.source.pk,
            "target_account": self.target.pk,
            "code": _SUBSCRIPTION_LAPIS_YAML,
            "metadata": "{}",
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Agreement.objects.filter(source_account=self.source, target_account=self.target).exists())
        self.assertTrue(Contract.objects.filter(name__contains="view-src").exists())

    def test_detail_shows_uuid_accounts_contract_actions(self):
        from django.urls import reverse
        from .models import Agreement, Contract
        self._login()
        contract = Contract.objects.create(name="SubContract", code=_SUBSCRIPTION_LAPIS_YAML)
        agr = Agreement.objects.create(
            source_account=self.source,
            target_account=self.target,
            contract=contract,
        )
        resp = self.client.get(reverse("assets:agreement_detail", args=[agr.pk]))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn(str(agr.uuid), content)
        self.assertIn("view-src", content)
        self.assertIn("view-tgt", content)
        self.assertIn("SubContract", content)
        self.assertIn("bill_period", content)

    def test_create_post_invalid_yaml_shows_error(self):
        from django.urls import reverse
        self._login()
        resp = self.client.post(reverse("assets:agreement_create"), {
            "source_account": self.source.pk,
            "target_account": self.target.pk,
            "code": ": bad yaml {{{{",
            "metadata": "{}",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"code", resp.content.lower())


# ---------------------------------------------------------------------------
# Agreement dry-run view tests
# ---------------------------------------------------------------------------

@override_settings(STATICFILES_STORAGE=_SIMPLE_STATIC)
class AgreementDryRunTests(TestCase):
    def setUp(self):
        from decimal import Decimal
        from django.contrib.auth import get_user_model
        _make_platform()
        User = get_user_model()
        self.user = User.objects.create_user(username="druser", password="drpass")
        self.source = make_account("dr-source", "user")
        self.target = make_account("dr-target", "user")
        self.asset = create_asset(
            name="DryRun Token", unit_name="DRT",
            total_supply=Decimal("1000"), decimals=0,
            reserve_account=self.source, reference="create-drt",
        )
        from .models import Contract, Agreement
        self.contract = Contract.objects.create(
            name="SubscriptionDR", code=_SUBSCRIPTION_LAPIS_YAML
        )
        self.agreement = Agreement.objects.create(
            source_account=self.source,
            target_account=self.target,
            contract=self.contract,
        )

    def _login(self):
        self.client.force_login(self.user)

    def _dry_run(self, action, global_state=None, app_args=None):
        import json
        from django.urls import reverse
        payload = {
            "action": action,
            "global_state": global_state or {},
            "local_state": {},
            "boxes": {},
            "transaction": {},
            "group_transactions": [],
            "global_fields": {},
            "app_args": app_args or [],
        }
        return self.client.post(
            reverse("assets:agreement_dry_run", args=[self.agreement.pk]),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_dry_run_requires_login(self):
        from django.urls import reverse
        resp = self.client.post(reverse("assets:agreement_dry_run", args=[self.agreement.pk]))
        self.assertNotEqual(resp.status_code, 200)

    def test_dry_run_activate_puts_global_status(self):
        self._login()
        resp = self._dry_run("activate")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["state"]["global"]["status"], "active")
        self.assertEqual(data["result"], "approve")

    def test_dry_run_activate_emits_log(self):
        self._login()
        resp = self._dry_run("activate")
        data = resp.json()
        self.assertIn("subscription_activated", data["effects"]["logs"])

    def test_dry_run_does_not_create_ledger_transaction(self):
        self._login()
        before = LedgerTransaction.objects.count()
        self._dry_run("activate")
        self.assertEqual(LedgerTransaction.objects.count(), before)

    def test_dry_run_does_not_create_ledger_entry(self):
        self._login()
        before = LedgerEntry.objects.count()
        self._dry_run("activate")
        self.assertEqual(LedgerEntry.objects.count(), before)

    def test_dry_run_does_not_create_obligation(self):
        from .models import Obligation
        self._login()
        before = Obligation.objects.count()
        self._dry_run("activate")
        self.assertEqual(Obligation.objects.count(), before)

    def test_dry_run_bill_period_returns_inner_transaction(self):
        self._login()
        resp = self._dry_run("bill_period", global_state={"status": "active"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        inner_txns = data["effects"]["inner_transactions"]
        self.assertEqual(len(inner_txns), 1)
        self.assertEqual(inner_txns[0]["TypeEnum"], 1)
        self.assertEqual(inner_txns[0]["Amount"], 1000)

    def test_dry_run_invalid_action_returns_error(self):
        self._login()
        resp = self._dry_run("nonexistent_action")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["ok"])

    def test_dry_run_assert_fails_when_state_wrong(self):
        self._login()
        resp = self._dry_run("bill_period", global_state={"status": "cancelled"})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["ok"])

    def test_dry_run_cancel_sets_cancelled_status(self):
        self._login()
        resp = self._dry_run("cancel")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["state"]["global"]["status"], "cancelled")
