"""Only the master creates assets, and only mirrors arrive on a branch.

Three layers are under test, because one is not enough: every real caller of
create_currency bypasses the service (views, ingress, trustline), and raw
Asset.objects.create bypasses Python entirely.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import override_settings

from toto.assets import issuer as issuer_module
from toto.assets.currency_hash import build_genesis, compute_currency_hash
from toto.assets.issuer import NotTheMaster
from toto.assets.models import AccountType, Asset, CurrencyIssuer, LedgerAccount
from toto.assets.services.assets import engrave_currency, mirror_asset
from toto.mint.services import create_currency
from toto.assets.testing import LedgerTestCase as TestCase


def _reserve(code="res") -> LedgerAccount:
    return LedgerAccount.objects.create(
        code=code, name="Reserve", account_type=AccountType.RESERVE)


class EngraveTests(TestCase):
    """ENGRAVE creates a standard, not money."""

    def test_engraving_creates_an_identity(self):
        asset = engrave_currency(name="Assarion", unit_name="ASR",
                                 max_supply=Decimal("100"), decimals=9)

        self.assertTrue(asset.currency_hash.startswith("tcur1:"))
        self.assertTrue(asset.verify_genesis())
        self.assertFalse(asset.is_mirror)

    def test_engraving_creates_no_units_at_all(self):
        from toto.assets.models import (AssetHolding, LedgerEntry,
                                        LedgerTransaction)

        asset = engrave_currency(name="Assarion", unit_name="ASR",
                                 max_supply=Decimal("100"), decimals=9)

        # An engraved, unminted currency is a legitimate state: the standard
        # exists and nobody holds any of it. Nothing was posted, so there is
        # nothing for a later MINT to double-count.
        self.assertEqual(AssetHolding.objects.filter(asset=asset).count(), 0)
        self.assertEqual(LedgerEntry.objects.filter(asset=asset).count(), 0)
        self.assertEqual(
            LedgerTransaction.objects.filter(asset=asset).count(), 0)

    def test_the_maximum_is_committed_to_the_identity(self):
        asset = engrave_currency(name="Assarion", unit_name="ASR",
                                 max_supply=Decimal("100"), decimals=9)

        self.assertEqual(asset.genesis_payload["max_supply_base_units"],
                         100 * 10 ** 9)
        self.assertEqual(compute_currency_hash(asset.genesis_payload),
                         asset.currency_hash)

    def test_a_branch_cannot_engrave(self):
        CurrencyIssuer.objects.filter(is_self=True).update(
            private_key_encrypted=None)
        before = Asset.objects.count()

        with self.assertRaises(NotTheMaster) as caught:
            engrave_currency(name="Forged", unit_name="FRG",
                             max_supply=Decimal("1"), decimals=0)
        self.assertIn("engrave a currency", str(caught.exception))
        self.assertEqual(Asset.objects.count(), before)

    def test_a_maximum_of_zero_is_refused(self):
        for bad in (Decimal("0"), Decimal("-1")):
            with self.subTest(maximum=bad):
                with self.assertRaises(ValidationError):
                    engrave_currency(name="Nothing", unit_name="NIL",
                                     max_supply=bad, decimals=0)

    def test_creating_an_asset_is_engraving_plus_a_reserve(self):
        # The old single verb is now the composite, so the identity half is
        # provably the same code path.
        asset = create_currency(
            name="Assarion", unit_name="ASR", total_supply=Decimal("100"),
            decimals=9, reserve_account=_reserve(), reference="create-asr")

        self.assertEqual(compute_currency_hash(asset.genesis_payload),
                         asset.currency_hash)
        self.assertEqual(asset.genesis_payload["max_supply_base_units"],
                         100 * 10 ** 9)


class MasterIssuesTests(TestCase):
    def test_a_created_asset_is_signed_and_verifies(self):
        asset = create_currency(
            name="Assarion", unit_name="ASR", total_supply=Decimal("100"),
            decimals=9, reserve_account=_reserve(), reference="create-asr")

        self.assertTrue(asset.currency_hash.startswith("tcur1:"))
        self.assertTrue(asset.genesis_signature)
        self.assertFalse(asset.is_mirror)
        self.assertTrue(asset.verify_genesis())

    def test_the_hash_commits_the_maximum_not_the_amount(self):
        asset = create_currency(
            name="Assarion", unit_name="ASR", total_supply=Decimal("100"),
            decimals=9, reserve_account=_reserve(), reference="create-asr")

        from toto.mint.history import supply

        self.assertEqual(asset.genesis_payload["max_supply_base_units"],
                         asset.max_supply_base_units)
        # The amount that exists is nowhere in the identity, even when a mint
        # has just made the two numbers equal.
        self.assertEqual(supply(asset), asset.max_supply_base_units)
        self.assertNotIn("supply_base_units", asset.genesis_payload)
        self.assertNotIn("minted_base_units", asset.genesis_payload)
        self.assertEqual(compute_currency_hash(asset.genesis_payload),
                         asset.currency_hash)

    def test_two_assets_with_the_same_description_get_different_identities(self):
        one = create_currency(name="A", unit_name="AAA", total_supply=Decimal("1"),
                           decimals=0, reserve_account=_reserve("r1"),
                           reference="c1")
        two = create_currency(name="A", unit_name="BBB", total_supply=Decimal("1"),
                           decimals=0, reserve_account=_reserve("r2"),
                           reference="c2")
        self.assertNotEqual(one.currency_hash, two.currency_hash)


class BranchesCreateNothingTests(TestCase):
    """Layer one: the service refuses outright on a non-master."""

    def test_without_an_issuer_key_creation_is_refused(self):
        CurrencyIssuer.objects.filter(is_self=True).update(
            private_key_encrypted=None)

        with self.assertRaises(NotTheMaster) as caught:
            create_currency(name="Forged", unit_name="FRG",
                         total_supply=Decimal("1"), decimals=0,
                         reserve_account=_reserve(), reference="forge")
        self.assertIn("signed mirrors", str(caught.exception))

    def test_the_refusal_leaves_no_half_created_asset(self):
        CurrencyIssuer.objects.filter(is_self=True).update(
            private_key_encrypted=None)
        before = Asset.objects.count()

        with self.assertRaises(NotTheMaster):
            create_currency(name="Forged", unit_name="FRG",
                         total_supply=Decimal("1"), decimals=0,
                         reserve_account=_reserve(), reference="forge")

        self.assertEqual(Asset.objects.count(), before)


class ConstraintTests(TestCase):
    """Layer three: the database, for callers that bypass Python entirely."""

    def test_an_asset_without_provenance_cannot_be_written(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Asset.objects.create(name="Bare", unit_name="BARE",
                                     decimals=0, max_supply_base_units=1)

    def test_an_empty_hash_is_refused_as_firmly_as_a_missing_one(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Asset.objects.create(name="Bare", unit_name="BARE2",
                                     decimals=0, max_supply_base_units=1,
                                     currency_hash="")


class IdentityIsImmutableTests(TestCase):
    """Layer two: once issued, an asset cannot become something else."""

    def setUp(self):
        self.asset = create_currency(
            name="Assarion", unit_name="ASR", total_supply=Decimal("100"),
            decimals=9, reserve_account=_reserve(), reference="create-asr")

    def test_the_ticker_cannot_be_changed(self):
        self.asset.unit_name = "OTHER"
        with self.assertRaises(ValidationError) as caught:
            self.asset.save()
        self.assertIn("unit_name", str(caught.exception))

    def test_the_supply_cannot_be_changed(self):
        # Supply is committed to the hash; moving it would make the identity
        # describe an amount that no longer exists.
        self.asset.max_supply_base_units += 1
        with self.assertRaises(ValidationError):
            self.asset.save()

    def test_mutable_fields_still_move(self):
        self.asset.active = False
        self.asset.backing_document = "Held in reserve."
        self.asset.save()
        self.asset.refresh_from_db()
        self.assertFalse(self.asset.active)


class MirrorTests(TestCase):
    """The only door onto a branch."""

    def setUp(self):
        self.master = issuer_module.local_issuer()
        self.genesis = build_genesis(
            issuer_fingerprint=self.master.fingerprint, unit_name="ASR",
            name="Assarion", decimals=9, max_supply_base_units=10 ** 11,
            issued_at="2026-08-11T12:00:00+00:00")
        self.signature = self.master.sign_genesis(self.genesis)

    def _pinned(self):
        """Turn this host into a branch: same authority, public half only.

        Not a second row — the fingerprint IS the public key, so a branch
        pinning its master holds a row with the same fingerprint and no
        private half. Stripping the key in place is what that looks like.
        """
        CurrencyIssuer.objects.filter(is_self=True).update(
            is_self=False, private_key_encrypted=None,
            label="Zenobia (pinned)")
        return CurrencyIssuer.objects.get(fingerprint=self.master.fingerprint)

    def test_a_signed_asset_mirrors(self):
        asset = mirror_asset(genesis_payload=self.genesis,
                             signature=self.signature, issuer=self.master,
                             origin_platform="zenobia")

        self.assertTrue(asset.is_mirror)
        self.assertEqual(asset.origin_platform, "zenobia")
        self.assertEqual(asset.currency_hash,
                         compute_currency_hash(self.genesis))
        self.assertTrue(asset.verify_genesis())

    def test_mirroring_is_idempotent_by_hash(self):
        first = mirror_asset(genesis_payload=self.genesis,
                             signature=self.signature, issuer=self.master)
        again = mirror_asset(genesis_payload=self.genesis,
                             signature=self.signature, issuer=self.master)
        self.assertEqual(first.pk, again.pk)

    def test_a_tampered_document_is_refused(self):
        tampered = dict(self.genesis, max_supply_base_units=10 ** 12)
        with self.assertRaises(ValidationError) as caught:
            mirror_asset(genesis_payload=tampered, signature=self.signature,
                         issuer=self.master)
        self.assertIn("does not verify", str(caught.exception))

    def test_nothing_is_written_when_verification_fails(self):
        tampered = dict(self.genesis, name="Something else")
        before = Asset.objects.count()
        with self.assertRaises(ValidationError):
            mirror_asset(genesis_payload=tampered, signature=self.signature,
                         issuer=self.master)
        self.assertEqual(Asset.objects.count(), before)

    def test_a_document_naming_another_issuer_is_refused(self):
        # Signed by us, but claiming someone else minted it.
        lying = dict(self.genesis, issuer_fingerprint="f" * 64)
        signature = self.master.sign_genesis(lying)
        with self.assertRaises(ValidationError) as caught:
            mirror_asset(genesis_payload=lying, signature=signature,
                         issuer=self.master)
        self.assertIn("different issuer", str(caught.exception))

    def test_a_branch_can_verify_with_only_the_public_half(self):
        pinned = self._pinned()
        asset = mirror_asset(genesis_payload=self.genesis,
                             signature=self.signature, issuer=pinned)
        self.assertTrue(asset.is_mirror)
        self.assertEqual(asset.issuer, pinned)
