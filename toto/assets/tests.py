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


class ContractFlowValidatorTests(TestCase):
    def _valid_tree(self):
        return {
            "language": "lapis", "version": 1, "kind": "contract_flow", "name": "T",
            "steps": [
                {"id": "start", "type": "start", "title": "Begin"},
                {"id": "end", "type": "stop", "title": "Done"},
            ],
        }

    def test_valid_flow_passes(self):
        from .lapis.compiler import ContractFlowValidator
        ContractFlowValidator().validate(self._valid_tree())

    def test_valid_contract_passes(self):
        from .lapis.compiler import ContractFlowValidator
        ContractFlowValidator().validate(self._valid_tree())

    def test_missing_language_raises(self):
        from .lapis.compiler import ContractFlowValidator
        from .lapis.exceptions import LapisValidationError
        tree = self._valid_tree()
        del tree["language"]
        with self.assertRaises(LapisValidationError):
            ContractFlowValidator().validate(tree)

    def test_wrong_version_raises(self):
        from .lapis.compiler import ContractFlowValidator
        from .lapis.exceptions import LapisValidationError
        tree = {**self._valid_tree(), "version": 99}
        with self.assertRaises(LapisValidationError):
            ContractFlowValidator().validate(tree)

    def test_missing_kind_raises(self):
        from .lapis.compiler import ContractFlowValidator
        from .lapis.exceptions import LapisValidationError
        tree = {**self._valid_tree()}
        del tree["kind"]
        with self.assertRaises(LapisValidationError):
            ContractFlowValidator().validate(tree)

    def test_empty_steps_raises(self):
        from .lapis.compiler import ContractFlowValidator
        from .lapis.exceptions import LapisValidationError
        tree = {**self._valid_tree(), "steps": []}
        with self.assertRaises(LapisValidationError):
            ContractFlowValidator().validate(tree)

    def test_unknown_step_type_raises(self):
        from .lapis.compiler import ContractFlowValidator
        from .lapis.exceptions import LapisValidationError
        tree = {**self._valid_tree(), "steps": [{"id": "s1", "type": "BOGUS", "title": "x"}]}
        with self.assertRaises(LapisValidationError):
            ContractFlowValidator().validate(tree)

    def test_step_missing_title_raises(self):
        from .lapis.compiler import ContractFlowValidator
        from .lapis.exceptions import LapisValidationError
        tree = {**self._valid_tree(), "steps": [{"id": "s1", "type": "start"}]}
        with self.assertRaises(LapisValidationError):
            ContractFlowValidator().validate(tree)

    def test_duplicate_step_id_raises(self):
        from .lapis.compiler import ContractFlowValidator
        from .lapis.exceptions import LapisValidationError
        tree = {**self._valid_tree(), "steps": [
            {"id": "s1", "type": "start", "title": "A"},
            {"id": "s1", "type": "stop", "title": "B"},
        ]}
        with self.assertRaises(LapisValidationError):
            ContractFlowValidator().validate(tree)

    def test_get_steps_returns_steps(self):
        from .lapis.compiler import ContractFlowValidator
        tree = self._valid_tree()
        steps = ContractFlowValidator().get_steps(tree)
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["id"], "start")


# ---------------------------------------------------------------------------
# Shared Lapis fixtures
# ---------------------------------------------------------------------------

_SIMPLE_LAPIS_YAML = """\
language: lapis
version: 1
kind: contract_flow
name: T
steps:
  - id: start
    type: start
    title: Begin
    next: end
  - id: end
    type: stop
    title: Done
"""

_SUBSCRIPTION_LAPIS_YAML = """\
language: lapis
version: 1
kind: contract_flow
name: Subscription
parties:
  - id: subscriber
    label: Subscriber
  - id: provider
    label: Provider
assets:
  - id: payment
    label: USDC
steps:
  - id: draft
    type: start
    title: Subscription created in draft status
    next: activate
  - id: activate
    type: condition
    title: Status is draft?
    then: set_active
    else: stop_invalid
  - id: set_active
    type: state
    title: Set status = active
    next: stop_activated
  - id: stop_activated
    type: stop
    title: Subscription is now active
  - id: stop_invalid
    type: stop
    title: Cannot activate — status is not draft
  - id: billing_trigger
    type: start
    title: Monthly billing cycle triggered
    next: check_active
  - id: check_active
    type: condition
    title: Subscription is active?
    then: make_payment
    else: stop_not_billable
  - id: make_payment
    type: payment
    title: Bill subscriber (1000 base units)
    from: subscriber
    to: provider
    asset: payment
    amount_base_units: 1000
    next: record_billing
  - id: record_billing
    type: record
    title: Log billing event
    next: stop_billed
  - id: stop_billed
    type: stop
    title: Period billed successfully
  - id: stop_not_billable
    type: stop
    title: Cannot bill — subscription not active
  - id: cancel_trigger
    type: start
    title: Cancellation requested
    next: set_cancelled
  - id: set_cancelled
    type: state
    title: Set status = cancelled
    next: stop_cancelled
  - id: stop_cancelled
    type: stop
    title: Subscription cancelled
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

    def test_missing_kind_fails(self):
        from django.core.exceptions import ValidationError
        from .models import Contract
        code = "language: lapis\nversion: 1\nname: X\nsteps:\n  - id: s\n    type: start\n    title: S\n"
        with self.assertRaises(ValidationError):
            Contract(name="MissingKind", code=code).full_clean()

    def test_unknown_step_type_fails(self):
        from django.core.exceptions import ValidationError
        from .models import Contract
        code = (
            "language: lapis\nversion: 1\nkind: contract_flow\nname: X\n"
            "steps:\n  - id: s\n    type: BOGUS\n    title: x\n"
        )
        with self.assertRaises(ValidationError):
            Contract(name="BadStep", code=code).full_clean()

    def test_step_ids_extractable(self):
        from .lapis.loader import loads_contract
        from .lapis.compiler import ContractFlowValidator
        tree = loads_contract(_SUBSCRIPTION_LAPIS_YAML, fmt="yaml")
        steps = ContractFlowValidator().get_steps(tree)
        step_ids = [s["id"] for s in steps]
        self.assertIn("draft", step_ids)
        self.assertIn("activate", step_ids)

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
            "language: lapis\nversion: 1\nname: X\n"
            "steps:\n  - id: s\n    type: BOGUS\n    title: x\n"
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

    def test_detail_shows_uuid_accounts_contract_steps(self):
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
# Contract view tests
# ---------------------------------------------------------------------------

@override_settings(STATICFILES_STORAGE=_SIMPLE_STATIC)
class ContractViewTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        _make_platform()
        User = get_user_model()
        self.user = User.objects.create_user(username="cvu", password="cvpass")
        from .models import Contract
        self.contract = Contract.objects.create(name="TestContract", code=_SUBSCRIPTION_LAPIS_YAML)

    def _login(self):
        self.client.force_login(self.user)

    def _url(self, name, *args):
        from django.urls import reverse
        return reverse(f"assets:{name}", args=args)

    def test_list_requires_login(self):
        resp = self.client.get(self._url("contract_list"))
        self.assertNotEqual(resp.status_code, 200)

    def test_create_requires_login(self):
        resp = self.client.get(self._url("contract_create"))
        self.assertNotEqual(resp.status_code, 200)

    def test_detail_requires_login(self):
        resp = self.client.get(self._url("contract_detail", self.contract.uuid))
        self.assertNotEqual(resp.status_code, 200)

    def test_list_shows_contracts(self):
        self._login()
        resp = self.client.get(self._url("contract_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"TestContract", resp.content)

    def test_detail_shows_name_and_steps(self):
        self._login()
        resp = self.client.get(self._url("contract_detail", self.contract.uuid))
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn("TestContract", content)
        self.assertIn("draft", content)
        self.assertIn("activate", content)
        self.assertIn(str(self.contract.uuid), content)

    def test_create_get(self):
        self._login()
        resp = self.client.get(self._url("contract_create"))
        self.assertEqual(resp.status_code, 200)

    def test_create_post_valid_yaml(self):
        from .models import Contract
        self._login()
        resp = self.client.post(self._url("contract_create"), {
            "name": "Created via view",
            "code": _SIMPLE_LAPIS_YAML,
            "metadata": "{}",
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Contract.objects.filter(name="Created via view").exists())

    def test_create_post_invalid_yaml_shows_error(self):
        self._login()
        resp = self.client.post(self._url("contract_create"), {
            "name": "Bad contract",
            "code": ": bad yaml {{{{",
            "metadata": "{}",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"code", resp.content.lower())

    def test_update_get(self):
        self._login()
        resp = self.client.get(self._url("contract_update", self.contract.uuid))
        self.assertEqual(resp.status_code, 200)

    def test_update_post_saves(self):
        from .models import Contract
        self._login()
        resp = self.client.post(self._url("contract_update", self.contract.uuid), {
            "name": "Updated name",
            "code": _SIMPLE_LAPIS_YAML,
            "metadata": "{}",
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.contract.refresh_from_db()
        self.assertEqual(self.contract.name, "Updated name")

    def test_cytoscape_json_returns_nodes_edges(self):
        self._login()
        resp = self.client.get(self._url("contract_cytoscape_json", self.contract.uuid))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("nodes", data)
        self.assertIn("edges", data)
        self.assertGreater(len(data["nodes"]), 0)

    def test_cytoscape_empty_contract_returns_empty(self):
        from .models import Contract
        c = Contract.objects.create(name="Empty")
        self._login()
        resp = self.client.get(self._url("contract_cytoscape_json", c.uuid))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["nodes"], [])

    def test_ingress_contracts_appear_in_list(self):
        from io import StringIO
        from django.core.management import call_command
        from .models import Contract
        call_command("ingress_instruments", "--deploy-contracts", stdout=StringIO())
        self._login()
        resp = self.client.get(self._url("contract_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Subscription:demo-subscription-001", resp.content)

    def test_linked_instrument_appears_in_detail(self):
        from io import StringIO
        from django.core.management import call_command
        from .models import Contract
        from toto.instruments.models import FinancialInstrument
        call_command("ingress_instruments", "--deploy-contracts", stdout=StringIO())
        instr = FinancialInstrument.objects.get(reference="demo-subscription-001")
        contract = instr.contract
        self._login()
        resp = self.client.get(self._url("contract_detail", contract.uuid))
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"demo-subscription-001", resp.content)


# ---------------------------------------------------------------------------
# Obligation helpers
# ---------------------------------------------------------------------------

class ObligationHelperTests(TestCase):
    def setUp(self):
        from .services.assets import create_asset
        self.reserve = make_account("reserve-obh", "reserve")
        self.alice = make_account("alice-obh")
        self.bob = make_account("bob-obh")
        self.asset = create_asset(
            name="ObHAsset", unit_name="OBH", total_supply=Decimal("1000"),
            decimals=2, reserve_account=self.reserve, reference="create-obh",
        )

    def _make_obligation(self, debtor, creditor, amount=100):
        from django.utils import timezone
        from .models import Obligation
        return Obligation.objects.create(
            reference=f"obh-{debtor.code}-{creditor.code}",
            debtor_account=debtor,
            creditor_account=creditor,
            asset=self.asset,
            amount_base_units=amount,
            due_at=timezone.now(),
        )

    def test_is_payable_for(self):
        ob = self._make_obligation(self.alice, self.bob)
        self.assertTrue(ob.is_payable_for(self.alice))
        self.assertFalse(ob.is_payable_for(self.bob))

    def test_is_receivable_for(self):
        ob = self._make_obligation(self.alice, self.bob)
        self.assertTrue(ob.is_receivable_for(self.bob))
        self.assertFalse(ob.is_receivable_for(self.alice))

    def test_payables_for_queryset(self):
        from .models import Obligation
        self._make_obligation(self.alice, self.bob)
        self._make_obligation(self.bob, self.alice, amount=200)
        self.assertEqual(Obligation.objects.payables_for(self.alice).count(), 1)

    def test_receivables_for_queryset(self):
        from .models import Obligation
        self._make_obligation(self.alice, self.bob)
        self.assertEqual(Obligation.objects.receivables_for(self.bob).count(), 1)
        self.assertEqual(Obligation.objects.receivables_for(self.alice).count(), 0)

    def test_pending_queryset(self):
        from .models import Obligation, ObligationStatus
        from django.utils import timezone
        ob = self._make_obligation(self.alice, self.bob)
        self.assertEqual(Obligation.objects.pending().count(), 1)
        ob.status = ObligationStatus.FULFILLED
        ob.fulfilled_at = timezone.now()
        ob.save()
        self.assertEqual(Obligation.objects.pending().count(), 0)

    def test_overdue_queryset(self):
        from .models import Obligation
        from django.utils import timezone
        import datetime
        ob = self._make_obligation(self.alice, self.bob)
        ob.due_at = timezone.now() - datetime.timedelta(days=1)
        ob.save()
        self.assertEqual(Obligation.objects.overdue().count(), 1)


# ---------------------------------------------------------------------------
# Schedule model
# ---------------------------------------------------------------------------

class ScheduleModelTests(TestCase):
    def _make_schedule(self, **kwargs):
        from toto.claims.models import Schedule, ScheduleKind, ScheduleStatus
        from django.utils import timezone
        defaults = dict(
            name="Test schedule",
            kind=ScheduleKind.BILLING,
            status=ScheduleStatus.ACTIVE,
            starts_at=timezone.now(),
            source_type="test",
            source_id="1",
        )
        defaults.update(kwargs)
        return Schedule.objects.create(**defaults)

    def test_create_schedule(self):
        from toto.claims.models import Schedule
        s = self._make_schedule()
        self.assertIsNotNone(s.pk)
        self.assertEqual(s.name, "Test schedule")

    def test_ends_at_before_starts_at_fails_clean(self):
        from toto.claims.models import Schedule, ScheduleKind
        from django.core.exceptions import ValidationError
        from django.utils import timezone
        import datetime
        now = timezone.now()
        s = Schedule(
            name="bad",
            kind=ScheduleKind.BILLING,
            starts_at=now,
            ends_at=now - datetime.timedelta(days=1),
            source_type="test",
            source_id="1",
        )
        with self.assertRaises(ValidationError):
            s.clean()

    def test_no_reference_fails_clean(self):
        from toto.claims.models import Schedule, ScheduleKind
        from django.core.exceptions import ValidationError
        from django.utils import timezone
        s = Schedule(
            name="no-ref",
            kind=ScheduleKind.BILLING,
            starts_at=timezone.now(),
        )
        with self.assertRaises(ValidationError):
            s.clean()

    def test_manual_metadata_bypasses_ref_check(self):
        from toto.claims.models import Schedule, ScheduleKind
        from django.utils import timezone
        s = Schedule(
            name="manual",
            kind=ScheduleKind.BILLING,
            starts_at=timezone.now(),
            metadata={"manual": True},
        )
        s.clean()  # should not raise

    def test_active_queryset(self):
        from toto.claims.models import Schedule, ScheduleStatus
        self._make_schedule(status=ScheduleStatus.ACTIVE)
        self._make_schedule(name="paused", status=ScheduleStatus.PAUSED)
        from toto.claims.models import Schedule
        self.assertEqual(Schedule.objects.active().count(), 1)

    def test_due_queryset(self):
        from toto.claims.models import Schedule, ScheduleStatus
        from django.utils import timezone
        import datetime
        now = timezone.now()
        self._make_schedule(next_run_at=now - datetime.timedelta(hours=1))
        self._make_schedule(name="future", next_run_at=now + datetime.timedelta(days=1))
        from toto.claims.models import Schedule
        self.assertEqual(Schedule.objects.due(now=now).count(), 1)

    def test_by_kind_queryset(self):
        from toto.claims.models import Schedule, ScheduleKind
        self._make_schedule(kind=ScheduleKind.BILLING)
        self._make_schedule(name="other", kind=ScheduleKind.RENEWAL)
        from toto.claims.models import Schedule
        self.assertEqual(Schedule.objects.by_kind(ScheduleKind.BILLING).count(), 1)

    def test_for_source_queryset(self):
        from toto.claims.models import Schedule
        self._make_schedule(source_type="instruments.FI", source_id="42")
        self._make_schedule(name="other", source_type="instruments.FI", source_id="99")
        self.assertEqual(Schedule.objects.for_source("instruments.FI", "42").count(), 1)


# ---------------------------------------------------------------------------
# Condition model and service helpers
# ---------------------------------------------------------------------------

class ConditionModelTests(TestCase):
    def _make_condition(self, **kwargs):
        from toto.claims.models import Condition, ConditionKind, ConditionStatus
        defaults = dict(
            name="Test condition",
            kind=ConditionKind.TIME,
            status=ConditionStatus.PENDING,
            source_type="test",
            source_id="1",
        )
        defaults.update(kwargs)
        return Condition.objects.create(**defaults)

    def test_create_condition(self):
        c = self._make_condition()
        self.assertIsNotNone(c.pk)

    def test_no_reference_fails_clean(self):
        from toto.claims.models import Condition, ConditionKind
        from django.core.exceptions import ValidationError
        c = Condition(name="no-ref", kind=ConditionKind.TIME)
        with self.assertRaises(ValidationError):
            c.clean()

    def test_mark_satisfied(self):
        from toto.claims.models import ConditionStatus
        from .services.lifecycle import mark_condition_satisfied
        c = self._make_condition()
        mark_condition_satisfied(c)
        c.refresh_from_db()
        self.assertEqual(c.status, ConditionStatus.SATISFIED)
        self.assertIsNotNone(c.satisfied_at)

    def test_mark_failed(self):
        from toto.claims.models import ConditionStatus
        from .services.lifecycle import mark_condition_failed
        c = self._make_condition()
        mark_condition_failed(c, reason="bad state")
        c.refresh_from_db()
        self.assertEqual(c.status, ConditionStatus.FAILED)
        self.assertIsNotNone(c.failed_at)
        self.assertEqual(c.metadata.get("failure_reason"), "bad state")

    def test_waive_condition(self):
        from toto.claims.models import ConditionStatus
        from .services.lifecycle import waive_condition
        c = self._make_condition()
        waive_condition(c, reason="approved manually")
        c.refresh_from_db()
        self.assertEqual(c.status, ConditionStatus.WAIVED)
        self.assertEqual(c.metadata.get("waive_reason"), "approved manually")

    def test_evaluate_always_true(self):
        from .services.lifecycle import evaluate_condition
        from toto.claims.models import ConditionKind
        c = self._make_condition(kind=ConditionKind.MANUAL, expression={"type": "always_true"})
        self.assertTrue(evaluate_condition(c))

    def test_evaluate_status_equals(self):
        from .services.lifecycle import evaluate_condition
        from toto.claims.models import ConditionKind
        c = self._make_condition(kind=ConditionKind.STATUS, expression={"type": "status_equals", "value": "active"})
        self.assertTrue(evaluate_condition(c, context={"status": "active"}))
        self.assertFalse(evaluate_condition(c, context={"status": "draft"}))

    def test_evaluate_now_after_past(self):
        from .services.lifecycle import evaluate_condition
        from toto.claims.models import ConditionKind
        import datetime
        from django.utils import timezone
        past = (timezone.now() - datetime.timedelta(days=1)).isoformat()
        c = self._make_condition(kind=ConditionKind.TIME, expression={"type": "now_after", "datetime": past})
        self.assertTrue(evaluate_condition(c))

    def test_evaluate_now_before_future(self):
        from .services.lifecycle import evaluate_condition
        from toto.claims.models import ConditionKind
        import datetime
        from django.utils import timezone
        future = (timezone.now() + datetime.timedelta(days=1)).isoformat()
        c = self._make_condition(kind=ConditionKind.TIME, expression={"type": "now_before", "datetime": future})
        self.assertTrue(evaluate_condition(c))

    def test_evaluate_balance_at_least(self):
        from .services.lifecycle import evaluate_condition
        from toto.claims.models import ConditionKind
        c = self._make_condition(kind=ConditionKind.BALANCE, expression={"type": "balance_at_least", "amount": 100})
        self.assertTrue(evaluate_condition(c, context={"balance": 200}))
        self.assertFalse(evaluate_condition(c, context={"balance": 50}))

    def test_evaluate_unsupported_strict_raises(self):
        from .services.lifecycle import evaluate_condition
        from toto.claims.models import ConditionKind
        from django.core.exceptions import ValidationError
        c = self._make_condition(kind=ConditionKind.OTHER, expression={"type": "unknown_type"})
        with self.assertRaises(ValidationError):
            evaluate_condition(c, strict=True)

    def test_evaluate_unsupported_returns_false(self):
        from .services.lifecycle import evaluate_condition
        from toto.claims.models import ConditionKind
        c = self._make_condition(kind=ConditionKind.OTHER, expression={"type": "unknown_type"})
        self.assertFalse(evaluate_condition(c))


# ---------------------------------------------------------------------------
# Allocation model and service helpers
# ---------------------------------------------------------------------------

class AllocationModelTests(TestCase):
    def setUp(self):
        from .services.assets import create_asset
        self.reserve = make_account("reserve-alloc", "reserve")
        self.alice = make_account("alice-alloc")
        self.bob = make_account("bob-alloc")
        self.asset = create_asset(
            name="AllocAsset", unit_name="ALC", total_supply=Decimal("1000"),
            decimals=2, reserve_account=self.reserve, reference="create-alc",
        )

    def _make_allocation(self, **kwargs):
        from toto.claims.models import Allocation, AllocationKind, AllocationStatus
        defaults = dict(
            kind=AllocationKind.RESERVE,
            status=AllocationStatus.ACTIVE,
            asset=self.asset,
            amount_base_units=1000,
            allocated_amount_base_units=1000,
            source_type="test",
            source_id="1",
        )
        defaults.update(kwargs)
        return Allocation.objects.create(**defaults)

    def test_create_allocation(self):
        a = self._make_allocation()
        self.assertIsNotNone(a.pk)

    def test_negative_amount_fails_clean(self):
        from toto.claims.models import Allocation, AllocationKind
        from django.core.exceptions import ValidationError
        a = Allocation(
            kind=AllocationKind.RESERVE,
            asset=self.asset,
            amount_base_units=-1,
            source_type="test",
            source_id="1",
        )
        with self.assertRaises(ValidationError):
            a.clean()

    def test_zero_amount_fails_clean(self):
        from toto.claims.models import Allocation, AllocationKind
        from django.core.exceptions import ValidationError
        a = Allocation(
            kind=AllocationKind.RESERVE,
            asset=self.asset,
            amount_base_units=0,
            source_type="test",
            source_id="1",
        )
        with self.assertRaises(ValidationError):
            a.clean()

    def test_released_plus_consumed_exceeds_amount_fails_clean(self):
        from toto.claims.models import Allocation, AllocationKind
        from django.core.exceptions import ValidationError
        a = Allocation(
            kind=AllocationKind.RESERVE,
            asset=self.asset,
            amount_base_units=100,
            released_amount_base_units=60,
            consumed_amount_base_units=60,
            source_type="test",
            source_id="1",
        )
        with self.assertRaises(ValidationError):
            a.clean()

    def test_remaining_amount(self):
        a = self._make_allocation(amount_base_units=1000, released_amount_base_units=200, consumed_amount_base_units=100)
        self.assertEqual(a.remaining_amount_base_units, 700)

    def test_is_active_now(self):
        a = self._make_allocation()
        self.assertTrue(a.is_active_now)

    def test_activate_allocation(self):
        from toto.claims.models import Allocation, AllocationKind, AllocationStatus
        from .services.lifecycle import activate_allocation
        a = self._make_allocation(status=AllocationStatus.DRAFT, allocated_amount_base_units=0)
        activate_allocation(a)
        a.refresh_from_db()
        self.assertEqual(a.status, AllocationStatus.ACTIVE)
        self.assertEqual(a.allocated_amount_base_units, 1000)

    def test_release_allocation(self):
        from toto.claims.models import AllocationStatus
        from .services.lifecycle import release_allocation
        a = self._make_allocation(amount_base_units=100)
        release_allocation(a, 100)
        a.refresh_from_db()
        self.assertEqual(a.released_amount_base_units, 100)
        self.assertEqual(a.status, AllocationStatus.RELEASED)

    def test_consume_allocation(self):
        from toto.claims.models import AllocationStatus
        from .services.lifecycle import consume_allocation
        a = self._make_allocation(amount_base_units=100)
        consume_allocation(a, 100)
        a.refresh_from_db()
        self.assertEqual(a.consumed_amount_base_units, 100)
        self.assertEqual(a.status, AllocationStatus.CONSUMED)

    def test_cancel_allocation(self):
        from toto.claims.models import AllocationStatus
        from .services.lifecycle import cancel_allocation
        a = self._make_allocation()
        cancel_allocation(a)
        a.refresh_from_db()
        self.assertEqual(a.status, AllocationStatus.CANCELLED)


# ---------------------------------------------------------------------------
# ContractEvent creation
# ---------------------------------------------------------------------------

class ContractEventTests(TestCase):
    def test_create_event_with_source(self):
        from toto.claims.models import ContractEventKind
        from .services.lifecycle import create_event
        ev = create_event(
            kind=ContractEventKind.CREATED,
            title="Test event",
            source_type="test",
            source_id="42",
        )
        self.assertIsNotNone(ev.pk)
        self.assertEqual(ev.kind, ContractEventKind.CREATED)

    def test_create_obligation_event(self):
        from .models import Obligation
        from toto.claims.models import ContractEventKind
        from .services.lifecycle import create_obligation_event
        from .services.assets import create_asset
        from django.utils import timezone
        reserve = make_account("reserve-cev", "reserve")
        alice = make_account("alice-cev")
        bob = make_account("bob-cev")
        asset = create_asset(
            name="CevAsset", unit_name="CEV", total_supply=Decimal("100"),
            decimals=0, reserve_account=reserve, reference="create-cev",
        )
        ob = Obligation.objects.create(
            reference="cev-ob-1", debtor_account=alice, creditor_account=bob,
            asset=asset, amount_base_units=10, due_at=timezone.now(),
        )
        ev = create_obligation_event(ob)
        self.assertEqual(ev.kind, ContractEventKind.OBLIGATION_CREATED)
        self.assertEqual(ev.obligation, ob)

    def test_create_checkpoint(self):
        from toto.claims.models import ContractEventKind
        from .services.lifecycle import create_checkpoint
        ev = create_checkpoint("test.Model", 99, {"note": "hello"})
        self.assertEqual(ev.source_type, "test.Model")
        self.assertEqual(ev.source_id, "99")
        self.assertTrue(ev.payload.get("checkpoint"))

    def test_event_clean_requires_reference(self):
        from toto.claims.models import ContractEvent, ContractEventKind
        from django.core.exceptions import ValidationError
        ev = ContractEvent(kind=ContractEventKind.CREATED, title="no-ref")
        with self.assertRaises(ValidationError):
            ev.clean()

    def test_event_clean_manual_payload_bypasses(self):
        from toto.claims.models import ContractEvent, ContractEventKind
        ev = ContractEvent(kind=ContractEventKind.CREATED, title="ok", payload={"manual": True})
        ev.clean()  # should not raise


# View tests for lifecycle primitives will live in the dedicated claims app.


# ---------------------------------------------------------------------------
# Signing helpers
# ---------------------------------------------------------------------------

def _generate_ed25519_pem_pair():
    """Return (private_key_pem str, public_key_pem str) for Ed25519."""
    from cryptography.hazmat.primitives.asymmetric import ed25519 as _ed
    from cryptography.hazmat.primitives import serialization as _ser
    priv = _ed.Ed25519PrivateKey.generate()
    priv_pem = priv.private_bytes(
        _ser.Encoding.PEM, _ser.PrivateFormat.PKCS8, _ser.NoEncryption()
    ).decode()
    pub_pem = priv.public_key().public_bytes(
        _ser.Encoding.PEM, _ser.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return priv_pem, pub_pem


_strongbox_counter = 0


def _make_gervazy_session_and_epk(user, private_key_pem, public_key_pem, key_id="test-key-1"):
    """Create a Gervazy strongbox + EncryptedPrivateKey; return (session, epk)."""
    global _strongbox_counter
    _strongbox_counter += 1
    from toto.gervazy.crypto import GervazyCryptoSession
    session, wrapped_key = GervazyCryptoSession.initialize_strongbox(
        user, f"test-strongbox-{_strongbox_counter}", "testpassword"
    )
    epk = session.encrypt_private_key(
        wrapped_key,
        private_key_pem,
        key_id=key_id,
        key_type="Ed25519",
        public_key_pem=public_key_pem,
    )
    return session, epk


def _make_ledger_account_key(ledger_account, epk, public_key_pem):
    from django.utils import timezone
    from .models import LedgerAccountKey
    return LedgerAccountKey.objects.create(
        ledger_account=ledger_account,
        encrypted_private_key=epk,
        key_id=epk.key_id,
        public_key_pem=public_key_pem,
        algorithm="Ed25519",
        valid_from=timezone.now(),
    )


def _build_simple_tx(asset, sender, receiver, amount_base_units=100, reference="sign-tx-1"):
    """Create an unposted transfer transaction with two entries."""
    from .models import LedgerTransaction, LedgerEntry, TransactionType
    tx = LedgerTransaction.objects.create(
        reference=reference,
        transaction_type=TransactionType.ASSET_TRANSFER,
        asset=asset,
    )
    LedgerEntry.objects.create(transaction=tx, account=sender, asset=asset, amount_base_units=-amount_base_units)
    LedgerEntry.objects.create(transaction=tx, account=receiver, asset=asset, amount_base_units=amount_base_units)
    return tx


# ---------------------------------------------------------------------------
# Signing service tests
# ---------------------------------------------------------------------------

class SigningServiceTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.user = User.objects.create_user(username="signer", password="pass")
        self.reserve = make_account("sign-reserve", "reserve")
        self.alice = make_account("sign-alice")
        self.bob = make_account("sign-bob")
        self.asset = create_asset(
            name="SignAsset", unit_name="SGN", total_supply=Decimal("10000"),
            decimals=2, reserve_account=self.reserve, reference="create-sgn",
        )
        self.priv_pem, self.pub_pem = _generate_ed25519_pem_pair()
        self.session, self.epk = _make_gervazy_session_and_epk(self.user, self.priv_pem, self.pub_pem)
        self.account_key = _make_ledger_account_key(self.reserve, self.epk, self.pub_pem)

    def test_sign_and_verify_direct(self):
        from .signing import sign_transaction_directly, verify_transaction
        tx = _build_simple_tx(self.asset, self.reserve, self.alice, reference="sgn-direct-1")
        sign_transaction_directly(tx, self.account_key, self.session)
        tx.save()
        ok, msg = verify_transaction(tx)
        self.assertTrue(ok, msg)

    def test_verify_tampered_payload_fails(self):
        from .signing import sign_transaction_directly, verify_transaction
        tx = _build_simple_tx(self.asset, self.reserve, self.alice, reference="sgn-tamper-1")
        sign_transaction_directly(tx, self.account_key, self.session)
        tx.save()
        # Tamper a field without re-signing.
        tx.entries.filter(amount_base_units=-100).update(amount_base_units=-999)
        ok, msg = verify_transaction(tx)
        self.assertFalse(ok)

    def test_unsigned_transaction_fails_verify(self):
        from .signing import verify_transaction
        tx = _build_simple_tx(self.asset, self.reserve, self.alice, reference="sgn-unsigned-1")
        ok, msg = verify_transaction(tx)
        self.assertFalse(ok)
        self.assertIn("not signed", msg)

    def test_idempotency_key_set_on_sign(self):
        from .signing import sign_transaction_directly
        tx = _build_simple_tx(self.asset, self.reserve, self.alice, reference="sgn-idem-1")
        sign_transaction_directly(tx, self.account_key, self.session)
        self.assertIsNotNone(tx.idempotency_key)

    def test_idempotency_key_preserved_if_already_set(self):
        from .signing import sign_transaction_directly
        tx = _build_simple_tx(self.asset, self.reserve, self.alice, reference="sgn-idem-2")
        tx.idempotency_key = "my-idempotency-key"
        tx.save()
        sign_transaction_directly(tx, self.account_key, self.session)
        self.assertEqual(tx.idempotency_key, "my-idempotency-key")

    def test_sign_posted_raises(self):
        from .signing import sign_transaction_directly
        tx = _build_simple_tx(self.asset, self.reserve, self.alice, reference="sgn-posted-1")
        # Post it directly via update to bypass immutability guard.
        from .models import LedgerTransaction
        LedgerTransaction.objects.filter(pk=tx.pk).update(posted=True)
        tx.refresh_from_db()
        with self.assertRaises(ValueError):
            sign_transaction_directly(tx, self.account_key, self.session)

    def test_inactive_key_raises(self):
        from .signing import sign_transaction_directly
        from .models import LedgerAccountKeyState
        self.account_key.state = LedgerAccountKeyState.SUSPENDED
        self.account_key.save()
        tx = _build_simple_tx(self.asset, self.reserve, self.alice, reference="sgn-inactive-1")
        with self.assertRaises(ValueError):
            sign_transaction_directly(tx, self.account_key, self.session)


class DelegatedSigningTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.utils import timezone
        User = get_user_model()
        self.user = User.objects.create_user(username="delegated", password="pass")
        self.reserve = make_account("del-reserve", "reserve")
        self.alice = make_account("del-alice")
        self.asset = create_asset(
            name="DelAsset", unit_name="DEL", total_supply=Decimal("10000"),
            decimals=2, reserve_account=self.reserve, reference="create-del",
        )
        # Account owner key.
        self.priv_pem, self.pub_pem = _generate_ed25519_pem_pair()
        self.session, self.epk = _make_gervazy_session_and_epk(self.user, self.priv_pem, self.pub_pem)
        self.account_key = _make_ledger_account_key(self.reserve, self.epk, self.pub_pem)

        # Delegate key (separate key material).
        self.del_priv_pem, self.del_pub_pem = _generate_ed25519_pem_pair()
        self.del_session, self.del_epk = _make_gervazy_session_and_epk(
            self.user, self.del_priv_pem, self.del_pub_pem, key_id="del-key-1"
        )

        from .models import LedgerAuthorization
        self.auth = LedgerAuthorization.objects.create(
            ledger_account=self.reserve,
            delegate_user=self.user,
            delegate_key=self.del_epk,
            scopes=["transfer"],
            max_amount_base_units=500,
            asset=self.asset,
            valid_from=timezone.now(),
            signed_by_account_key=self.account_key,
        )

    def test_delegated_sign_and_verify(self):
        from .signing import sign_transaction_delegated, verify_transaction
        tx = _build_simple_tx(self.asset, self.reserve, self.alice, amount_base_units=100, reference="del-sign-1")
        sign_transaction_delegated(tx, self.auth, self.del_session, scope="transfer")
        tx.save()
        ok, msg = verify_transaction(tx)
        self.assertTrue(ok, msg)

    def test_wrong_scope_raises(self):
        from .signing import sign_transaction_delegated
        tx = _build_simple_tx(self.asset, self.reserve, self.alice, reference="del-scope-1")
        with self.assertRaises(ValueError, msg="scope"):
            sign_transaction_delegated(tx, self.auth, self.del_session, scope="admin")

    def test_amount_exceeded_raises(self):
        from .signing import sign_transaction_delegated
        tx = _build_simple_tx(self.asset, self.reserve, self.alice, amount_base_units=1000, reference="del-amt-1")
        with self.assertRaises(ValueError, msg="ceiling"):
            sign_transaction_delegated(tx, self.auth, self.del_session, scope="transfer")

    def test_revoked_authorization_raises(self):
        from django.utils import timezone
        from .signing import sign_transaction_delegated
        self.auth.revoked_at = timezone.now()
        self.auth.save()
        tx = _build_simple_tx(self.asset, self.reserve, self.alice, reference="del-revoke-1")
        with self.assertRaises(ValueError):
            sign_transaction_delegated(tx, self.auth, self.del_session, scope="transfer")

    def test_expired_authorization_raises(self):
        import datetime
        from django.utils import timezone
        from .signing import sign_transaction_delegated
        self.auth.valid_until = timezone.now() - datetime.timedelta(seconds=1)
        self.auth.save()
        tx = _build_simple_tx(self.asset, self.reserve, self.alice, reference="del-exp-1")
        with self.assertRaises(ValueError):
            sign_transaction_delegated(tx, self.auth, self.del_session, scope="transfer")


class AuthorizationGrantSigningTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.utils import timezone
        User = get_user_model()
        self.user = User.objects.create_user(username="grant-signer", password="pass")
        self.reserve = make_account("grant-reserve", "reserve")
        self.asset = create_asset(
            name="GrantAsset", unit_name="GRT", total_supply=Decimal("100"),
            decimals=0, reserve_account=self.reserve, reference="create-grt",
        )
        self.priv_pem, self.pub_pem = _generate_ed25519_pem_pair()
        self.session, self.epk = _make_gervazy_session_and_epk(self.user, self.priv_pem, self.pub_pem)
        self.account_key = _make_ledger_account_key(self.reserve, self.epk, self.pub_pem)

        self.del_priv_pem, self.del_pub_pem = _generate_ed25519_pem_pair()
        self.del_session, self.del_epk = _make_gervazy_session_and_epk(
            self.user, self.del_priv_pem, self.del_pub_pem, key_id="grant-del-key-1"
        )

        from .models import LedgerAuthorization
        self.auth = LedgerAuthorization.objects.create(
            ledger_account=self.reserve,
            delegate_user=self.user,
            delegate_key=self.del_epk,
            scopes=["transfer"],
            valid_from=timezone.now(),
            signed_by_account_key=self.account_key,
        )

    def test_sign_grant_stores_payload_and_signature(self):
        from .signing import sign_authorization_grant
        sign_authorization_grant(self.auth, self.account_key, self.session)
        self.assertTrue(bool(self.auth.grant_signature))
        self.assertIn("scopes", self.auth.signed_grant_payload)

    def test_wrong_account_key_raises(self):
        from .signing import sign_authorization_grant
        other_priv, other_pub = _generate_ed25519_pem_pair()
        other_session, other_epk = _make_gervazy_session_and_epk(
            self.user, other_priv, other_pub, key_id="other-key-grant"
        )
        other_ledger = make_account("other-grant-acct")
        other_key = _make_ledger_account_key(other_ledger, other_epk, other_pub)
        with self.assertRaises(ValueError):
            sign_authorization_grant(self.auth, other_key, other_session)
