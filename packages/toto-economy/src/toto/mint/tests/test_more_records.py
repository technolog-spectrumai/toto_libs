"""The mint's records: the remote-audit fetch, the refusals in front of the
chain writer, the walk's findings, and the admin that cannot rewrite history.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from toto.assets.testing import LedgerTestCase as TestCase, make_asset
from toto.mint import audit, chain
from toto.mint.admin import CurrencyMintEventAdmin, IssuanceRecordAdmin
from toto.mint.chain import MintEventError, compute_event_hash, verify_event
from toto.mint.history import supply, verify_chain
from toto.mint.models import (CurrencyMintEvent, IssuanceRecord, LedgerAudit)
from toto.mint.services import append_event, burn, issue_asset, mint

HASH = "tcur1:" + "b" * 64


def _payload(**overrides):
    base = {"v": 1, "issuer_fingerprint": "f" * 64, "sequence": 0,
            "kind": "mint", "currency_hash": HASH, "amount_base_units": 5,
            "prev_hash": "", "issued_at": "2026-09-29T00:00:00+00:00"}
    base.update(overrides)
    return base


class FetchAttestationTests(SimpleTestCase):
    """The one outbound call of the audit, with ``requests.get`` mocked."""

    def _get(self, **response):
        return mock.patch("requests.get", return_value=mock.Mock(**response))

    def test_it_asks_the_branch_for_its_signed_statement(self):
        with self._get(status_code=200,
                       json=mock.Mock(return_value={"statement": {}})) as get:
            envelope, error = audit.fetch_attestation("https://branch.test/")
        self.assertEqual((envelope, error), ({"statement": {}}, ""))
        self.assertEqual(get.call_args.args[0],
                         "https://branch.test/assets/attestation.json")
        self.assertEqual(get.call_args.kwargs["timeout"], audit.TIMEOUT)

    def test_an_unreachable_branch_is_named_by_its_error(self):
        with mock.patch("requests.get", side_effect=ConnectionError("no route")):
            envelope, error = audit.fetch_attestation("https://branch.test")
        self.assertIsNone(envelope)
        self.assertEqual(error, "ConnectionError: no route")

    def test_a_non_200_answer_is_not_a_statement(self):
        with self._get(status_code=503):
            self.assertEqual(audit.fetch_attestation("https://b.test"),
                             (None, "HTTP 503"))

    def test_a_captive_portal_page_is_not_a_statement(self):
        with self._get(status_code=200,
                       json=mock.Mock(side_effect=ValueError("html"))):
            self.assertEqual(audit.fetch_attestation("https://b.test"),
                             (None, "the response was not JSON"))


class AuditRecordTests(TestCase):
    def test_an_unreachable_branch_is_recorded_through_the_real_fetch(self):
        from toto.assets.contracts import assign_contract
        from toto.assets.statement import UNREACHABLE

        asset = issue_asset(name="Audit coin", unit_name="AUD",
                            total_supply=Decimal("10"), decimals=0, reason="x")
        assign_contract(node="far-branch", asset=asset)
        with mock.patch("requests.get", side_effect=OSError("timed out")):
            row = audit.audit_branch(node="far-branch",
                                     base_url="https://far.test")
        self.assertEqual(row.outcome, UNREACHABLE)
        self.assertIn("timed out", row.findings[0])
        self.assertEqual(str(row), "far-branch: unreachable")

    def test_a_node_with_no_contract_is_never_fetched(self):
        with mock.patch("requests.get") as get:
            row = audit.audit_branch(node="stranger", base_url="https://x.test")
        get.assert_not_called()
        self.assertEqual(row.outcome, "unverifiable")
        self.assertEqual(LedgerAudit.objects.count(), 1)


class EventShapeTests(SimpleTestCase):
    def test_a_missing_field_is_named(self):
        payload = _payload()
        del payload["issued_at"]
        with self.assertRaisesMessage(MintEventError, "missing: issued_at"):
            compute_event_hash(payload)

    def test_extra_and_missing_fields_are_both_named(self):
        payload = _payload(memo="hi")
        del payload["kind"]
        with self.assertRaises(MintEventError) as caught:
            compute_event_hash(payload)
        self.assertIn("unexpected: memo", str(caught.exception))
        self.assertIn("missing: kind", str(caught.exception))

    def test_a_negative_or_boolean_sequence_is_refused(self):
        for sequence in (-1, True, "0"):
            with self.assertRaises(MintEventError):
                compute_event_hash(_payload(sequence=sequence))

    def test_a_boolean_amount_is_not_an_amount(self):
        with self.assertRaises(MintEventError):
            compute_event_hash(_payload(amount_base_units=True))

    def test_a_payload_that_is_not_json_native_is_refused(self):
        with self.assertRaisesMessage(MintEventError, "JSON-native"):
            compute_event_hash(_payload(issued_at=Decimal("1.5")))

    def test_a_good_signature_from_a_key_that_is_not_ed25519_is_refused(self):
        """Ed448 has the same verify(signature, data) shape as Ed25519, so a
        genuine Ed448 signature over the exact event bytes WOULD verify if the
        issuer's key type were not checked. The mint trusts Ed25519 only."""
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed448 import (
            Ed448PrivateKey)

        key = Ed448PrivateKey.generate()
        signature = chain.sign_event(key, _payload())
        # The signature really is good for this key over these bytes ...
        key.public_key().verify(bytes.fromhex(signature),
                                chain.framed(chain._canonical(_payload())))
        pem = key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        # ... and still the chain refuses it.
        self.assertFalse(verify_event(pem, _payload(), signature))

    def test_a_malformed_event_never_verifies(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey)

        key = Ed25519PrivateKey.generate()
        pem = key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        signature = chain.sign_event(key, _payload())
        self.assertTrue(verify_event(pem, _payload(), signature))
        self.assertFalse(verify_event(pem, _payload(kind="gift"), signature))


class AppendRefusalTests(TestCase):
    def setUp(self):
        super().setUp()
        self.asset = make_asset(unit_name="APR")

    def test_only_mint_and_burn_can_be_appended(self):
        with self.assertRaisesMessage(ValidationError, "not a monetary verb"):
            append_event(asset=self.asset, kind="airdrop",
                         amount_base_units=1, reason="free money")
        self.assertFalse(CurrencyMintEvent.objects.exists())

    def test_a_currency_without_a_genesis_hash_has_nothing_to_mint(self):
        hashless = SimpleNamespace(currency_hash="", unit_name="GHOST")
        with self.assertRaisesMessage(ValidationError, "no genesis hash"):
            append_event(asset=hashless, kind="mint", amount_base_units=1,
                         reason="from thin air")
        self.assertFalse(CurrencyMintEvent.objects.exists())

    def test_the_reason_is_stored_trimmed(self):
        event = append_event(asset=self.asset, kind="mint",
                             amount_base_units=1, reason="  because  ")
        self.assertEqual(event.reason, "because")


class AmountTests(TestCase):
    def setUp(self):
        super().setUp()
        self.asset = issue_asset(name="Amt", unit_name="AMT",
                                 total_supply=Decimal("100"), decimals=2,
                                 reason="x")

    def test_base_units_and_display_amounts_are_the_same_act(self):
        burn(asset=self.asset, amount=Decimal("1.5"), reason="display")
        burn(asset=self.asset, amount_base_units=150, reason="base")
        self.assertEqual(supply(self.asset), 10_000 - 300)

    def test_a_display_amount_finer_than_the_decimals_rounds_to_nothing(self):
        with self.assertRaisesMessage(ValidationError, "positive"):
            burn(asset=self.asset, amount=Decimal("0.001"), reason="dust")
        self.assertEqual(supply(self.asset), 10_000)

    def test_a_negative_mint_is_refused_not_turned_into_a_burn(self):
        burn(asset=self.asset, amount=Decimal("10"), reason="room")
        with self.assertRaises(ValidationError):
            mint(asset=self.asset, amount_base_units=-5, reason="sneaky burn")
        self.assertEqual(supply(self.asset), 9_000)


class ChainWalkTests(TestCase):
    def setUp(self):
        super().setUp()
        self.asset = make_asset(unit_name="WLK")

    def test_an_event_signed_by_an_unknown_issuer_is_a_finding(self):
        event = append_event(asset=self.asset, kind="mint",
                             amount_base_units=1, reason="x")
        CurrencyMintEvent.objects.filter(pk=event.pk).update(
            issuer_fingerprint="0" * 64)
        verdict = verify_chain()
        self.assertFalse(verdict)
        self.assertTrue(any("does not know" in f for f in verdict.findings),
                        verdict.findings)

    def test_a_payload_that_is_not_an_event_is_a_finding_not_a_crash(self):
        event = append_event(asset=self.asset, kind="mint",
                             amount_base_units=1, reason="x")
        CurrencyMintEvent.objects.filter(pk=event.pk).update(payload={"junk": 1})
        verdict = verify_chain()
        self.assertFalse(verdict)
        self.assertTrue(any("not an event at all" in f for f in verdict.findings),
                        verdict.findings)

    def test_a_sequence_gap_is_a_finding(self):
        append_event(asset=self.asset, kind="mint", amount_base_units=1,
                     reason="first")
        second = append_event(asset=self.asset, kind="mint",
                              amount_base_units=1, reason="second")
        CurrencyMintEvent.objects.filter(pk=second.pk).update(sequence=7)
        verdict = verify_chain()
        self.assertTrue(any("gap or a repeat" in f for f in verdict.findings),
                        verdict.findings)

    def test_one_row_verifies_on_its_own(self):
        event = append_event(asset=self.asset, kind="mint",
                             amount_base_units=1, reason="x")
        self.assertTrue(event.verify())

    def test_a_row_whose_issuer_is_gone_does_not_verify(self):
        event = append_event(asset=self.asset, kind="mint",
                             amount_base_units=1, reason="x")
        CurrencyMintEvent.objects.filter(pk=event.pk).update(
            issuer_fingerprint="1" * 64)
        event.refresh_from_db()
        self.assertFalse(event.verify())

    def test_a_row_whose_payload_was_altered_does_not_verify(self):
        event = append_event(asset=self.asset, kind="mint",
                             amount_base_units=1, reason="x")
        CurrencyMintEvent.objects.filter(pk=event.pk).update(
            payload=dict(event.payload, amount_base_units=10 ** 6))
        event.refresh_from_db()
        self.assertFalse(event.verify())

    def test_str_is_sequence_kind_and_amount(self):
        event = append_event(asset=self.asset, kind="mint",
                             amount_base_units=42, reason="x")
        self.assertTrue(str(event).startswith("#0 mint 42 tcur1:"))


class AdminTests(TestCase):
    def test_issuance_and_monetary_events_cannot_be_added_or_deleted(self):
        site = AdminSite()
        for admin in (IssuanceRecordAdmin(IssuanceRecord, site),
                      CurrencyMintEventAdmin(CurrencyMintEvent, site)):
            self.assertFalse(admin.has_add_permission(request=None))
            self.assertFalse(admin.has_delete_permission(request=None))

    def test_every_field_of_a_monetary_event_is_read_only(self):
        admin = CurrencyMintEventAdmin(CurrencyMintEvent, AdminSite())
        names = {f.name for f in CurrencyMintEvent._meta.fields}
        self.assertEqual(set(admin.readonly_fields), names)

    def test_an_issuance_record_reads_as_ticker_and_hash(self):
        asset = issue_asset(name="Str", unit_name="STR",
                            total_supply=Decimal("1"), decimals=0, reason="x")
        record = IssuanceRecord.objects.get(asset=asset)
        self.assertEqual(str(record), f"STR — {asset.currency_hash[:16]}…")
