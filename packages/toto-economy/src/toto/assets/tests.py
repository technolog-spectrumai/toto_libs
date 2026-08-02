from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

_SIMPLE_STATIC = "django.contrib.staticfiles.storage.StaticFilesStorage"

from .hashing import attach_hash, calculate_transaction_hash, verify_hash_chain
from .models import (
    Asset,
    Currency,
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

    def test_duplicate_reference_is_idempotent_for_identical_params(self):
        # Stripe semantics: replaying the SAME transfer is a retry and returns
        # the original transaction — no second movement, no error. This is what
        # makes crash-recovery re-runs and replayed clearing messages safe.
        first = transfer_asset(
            asset=self.asset, sender_account=self.reserve, receiver_account=self.alice,
            amount=Decimal("1.00"), reference="dup-txfr",
        )
        again = transfer_asset(
            asset=self.asset, sender_account=self.reserve, receiver_account=self.alice,
            amount=Decimal("1.00"), reference="dup-txfr",
        )
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(
            LedgerTransaction.objects.filter(reference="dup-txfr").count(), 1)
        # And the balance moved exactly once.
        self.assertEqual(get_asset_balance(self.asset, self.alice),
                         to_base_units(Decimal("1.00"), self.asset.decimals))

    def test_duplicate_reference_with_different_params_is_refused(self):
        # The same key naming a DIFFERENT movement is a reference collision —
        # the dangerous bug — and must never silently do either thing.
        from .services.assets import IdempotencyConflict

        transfer_asset(
            asset=self.asset, sender_account=self.reserve, receiver_account=self.alice,
            amount=Decimal("1.00"), reference="dup-txfr-2",
        )
        with self.assertRaises(IdempotencyConflict):
            transfer_asset(
                asset=self.asset, sender_account=self.reserve, receiver_account=self.alice,
                amount=Decimal("2.00"), reference="dup-txfr-2",
            )

    def test_transactions_carry_portable_identity(self):
        # The uuid is the cross-platform name of a transaction; origin fields
        # are blank on ordinary local activity and set only by clearing.
        a = transfer_asset(
            asset=self.asset, sender_account=self.reserve, receiver_account=self.alice,
            amount=Decimal("1.00"), reference="uuid-a",
        )
        b = transfer_asset(
            asset=self.asset, sender_account=self.reserve, receiver_account=self.alice,
            amount=Decimal("1.00"), reference="uuid-b",
        )
        self.assertIsNotNone(a.uuid)
        self.assertNotEqual(a.uuid, b.uuid)
        self.assertEqual(a.origin_platform, "")
        self.assertIsNone(a.origin_uuid)

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
# Standing alone
# ---------------------------------------------------------------------------

@override_settings(STATICFILES_STORAGE=_SIMPLE_STATIC)
class WithoutBourseTests(TestCase):
    """The ledger is its own feature: it must render with no exchange desk.

    BUILD_ASSETS and BUILD_BOURSE are separate flags, so assets/base.html cannot
    reverse a bourse URL unconditionally — that is a NoReverseMatch on every
    page of the app for anyone who built the ledger alone.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model
        _make_platform()
        user = get_user_model().objects.create_user(username="ledgeronly", password="pass")
        self.client.force_login(user)

    def test_wallet_renders_with_the_tab_hidden(self):
        from unittest.mock import patch
        from django.apps import apps
        from django.urls import reverse

        # Hide only bourse: `apps` is the global registry, so a blanket False
        # would also empty the URLConf the moment it is first imported.
        real = apps.is_installed
        with patch("toto.assets.context_processors.apps.is_installed",
                   side_effect=lambda label: False if label == "toto.bourse"
                   else real(label)):
            response = self.client.get(reverse("assets:wallet"))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"/bourse/", response.content)

    def test_the_tab_is_there_when_bourse_is(self):
        from django.apps import apps
        from django.urls import reverse

        if not apps.is_installed("toto.bourse"):
            self.skipTest("this build has no bourse")
        response = self.client.get(reverse("assets:wallet"))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"/bourse/", response.content)

    def test_context_processor_reports_the_registry(self):
        from django.apps import apps
        from toto.assets.context_processors import economy_apps

        self.assertEqual(economy_apps(None)["HAS_BOURSE"],
                         apps.is_installed("toto.bourse"))


# ---------------------------------------------------------------------------
# Ingress
# ---------------------------------------------------------------------------

class IngressAssetsTests(TestCase):
    """The seeder mints exactly two currencies: ASR and TPLN."""

    def setUp(self):
        from django.core.management import call_command
        from io import StringIO
        self.call = lambda: call_command("ingress_assets", stdout=StringIO(), stderr=StringIO())

    def test_seeds_asr_and_tpln_only(self):
        self.call()

        asr = Asset.objects.get(unit_name="ASR")
        tpln = Asset.objects.get(unit_name="TPLN")
        self.assertEqual(asr.name, "Assarion")
        self.assertEqual(asr.decimals, 9)
        self.assertEqual(asr.total_supply_display, Decimal("6666.666666667"))
        self.assertEqual(tpln.name, "Toto Złoty")
        self.assertEqual(tpln.decimals, 2)
        self.assertEqual(tpln.total_supply_display, Decimal("76658.70"))
        self.assertTrue(asr.is_currency)
        self.assertTrue(tpln.is_currency)

        self.assertFalse(Asset.objects.filter(unit_name="AUR").exists())
        self.assertEqual(
            sorted(Currency.objects.values_list("code", flat=True)),
            ["ASR", "TPLN"],
        )
        self.assertEqual(Currency.objects.get(code="TPLN").name, "Toto Złoty")

    def test_currency_wording_is_neutral(self):
        self.call()

        for asset in Asset.objects.filter(is_currency=True):
            # The descriptive text lives on the asset's creation transaction.
            creation = LedgerTransaction.objects.filter(
                asset=asset, transaction_type=TransactionType.ASSET_CREATE,
            ).first()
            blob = " ".join([
                asset.name, asset.backing_document, asset.minting_authority,
                str(asset.metadata), creation.description if creation else "",
            ]).lower()
            self.assertNotIn("stablecoin", blob)
            self.assertNotIn("peg", blob)

    def test_reseeding_is_idempotent(self):
        self.call()
        self.call()

        self.assertEqual(Asset.objects.filter(unit_name="ASR").count(), 1)
        self.assertEqual(Asset.objects.filter(unit_name="TPLN").count(), 1)
        self.assertEqual(Currency.objects.count(), 2)

    def test_a_base_build_has_exactly_two_assets(self):
        # ASR is the gas, TPLN is the unit of account. Everything else is demo
        # material and must not reach a real deployment.
        self.call()

        self.assertEqual(
            sorted(Asset.objects.values_list("unit_name", flat=True)),
            ["ASR", "TPLN"],
        )
        for asset in Asset.objects.all():
            self.assertNotEqual((asset.metadata or {}).get("kind"), "platform_token")

    def test_full_ingress_adds_the_demo_tokens(self):
        from django.core.management import call_command
        from io import StringIO

        call_command("ingress_assets", full=True, stdout=StringIO(), stderr=StringIO())

        units = set(Asset.objects.values_list("unit_name", flat=True))
        self.assertIn("BANANA", units)
        self.assertIn("MAKARONI", units)
        # Still not currencies — you cannot pay a bill in bananas by accident.
        self.assertEqual(
            sorted(Currency.objects.values_list("code", flat=True)), ["ASR", "TPLN"]
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


def _make_platform():
    from toto.core.models import Platform
    Platform.objects.get_or_create(
        site_name="Test",
        defaults={"author": "test", "publication_year": 2024, "active": True},
    )


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
