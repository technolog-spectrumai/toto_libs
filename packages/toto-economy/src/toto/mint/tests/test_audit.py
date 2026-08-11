"""The remote ledger audit. It records; it never punishes."""

from decimal import Decimal
from unittest import mock

from toto.assets import allocation, statement
from toto.assets.contracts import assign_contract
from toto.assets.models import AccountType, CurrencyContract, LedgerAccount
from toto.assets.services.assets import transfer_asset
from toto.assets.testing import LedgerTestCase as TestCase
from toto.mint import audit
from toto.mint.models import LedgerAudit
from toto.mint.services import issue_asset


class AuditTests(TestCase):
    def setUp(self):
        self.asset = issue_asset(name="Assarion", unit_name="ASR",
                                 total_supply=Decimal("1000"), decimals=2,
                                 reason="Gas.")
        assign_contract(node="placidia", asset=self.asset)
        allocation.allocate_to_branch(node="placidia", asset=self.asset,
                                      amount=Decimal("100"), reference="a1")

    def _branch_says(self, overrides=None):
        """What the branch would send. Built by hand, not from local state:
        this test runs on the MASTER, whose books are the other side of the
        movement — the branch's reserve does not exist in this database."""
        body = {
            "v": 1,
            "node_id": "placidia",
            "contract_serial": 1,
            "currency_hash": self.asset.currency_hash,
            "allocated_base_units": 10000,
            "drawn_base_units": 4000,
            "chain_head": "abc123",
        }
        if overrides:
            body.update(overrides)
        return statement.sign_statement(body)

    def _audit(self, envelope=None, error=""):
        result = (envelope, "") if envelope is not None else (None, error)
        with mock.patch.object(audit, "fetch_attestation", return_value=result):
            return audit.audit_branch(node="placidia",
                                      base_url="https://branch.example")

    def test_a_truthful_branch_passes(self):
        row = self._audit(self._branch_says())

        self.assertEqual(row.outcome, statement.OK)
        self.assertEqual(row.findings, [])
        self.assertEqual(row.expected_allocated_base_units, 10000)

    def test_an_over_drawing_branch_is_a_discrepancy(self):
        row = self._audit(self._branch_says({"drawn_base_units": 999999}))

        self.assertEqual(row.outcome, statement.DISCREPANCY)
        self.assertTrue(row.findings)

    def test_a_tampered_statement_is_unverifiable(self):
        envelope = self._branch_says()
        envelope["statement"]["drawn_base_units"] = 1
        self.assertEqual(self._audit(envelope).outcome, statement.UNVERIFIABLE)

    def test_a_branch_that_does_not_answer_is_unreachable_not_ok(self):
        # The distinction the whole design rests on: "I could not check" must
        # never read as "I checked and it is fine".
        row = self._audit(error="ConnectionError: refused")

        self.assertEqual(row.outcome, statement.UNREACHABLE)
        self.assertNotEqual(row.outcome, statement.OK)
        self.assertIn("Could not reach", row.findings[0])

    def test_a_platform_with_no_contract_is_unverifiable(self):
        CurrencyContract.objects.filter(node_id="placidia").update(
            superseded_at="2026-01-01T00:00:00+00:00")
        self.assertEqual(self._audit(self._branch_says()).outcome,
                         statement.UNVERIFIABLE)

    def test_a_failed_audit_changes_nothing_about_the_branch(self):
        # No automatic punishment. The contract survives, the allocation
        # survives; a person decides what to do.
        self._audit(self._branch_says({"drawn_base_units": 999999}))

        self.assertTrue(CurrencyContract.objects.filter(
            node_id="placidia", superseded_at__isnull=True).exists())

    def test_every_audit_is_recorded(self):
        self._audit(self._branch_says())
        self._audit(error="down")
        self.assertEqual(LedgerAudit.objects.count(), 2)

    def test_the_log_is_append_only(self):
        from django.core.exceptions import ValidationError

        row = self._audit(self._branch_says())
        row.outcome = statement.OK
        with self.assertRaises(ValidationError):
            row.save()
        with self.assertRaises(ValidationError):
            row.delete()


class AttestationEndpointTests(TestCase):
    def test_the_payload_is_signed_and_verifies(self):
        asset = issue_asset(name="A", unit_name="AAA",
                            total_supply=Decimal("10"), decimals=0,
                            reason="Gas.")
        assign_contract(node=__import__("toto.assets.contracts",
                                        fromlist=["x"]).node_id(), asset=asset)

        envelope = statement.sign_statement(statement.build_statement())
        result = statement.verify_statement(
            envelope, expected_node=envelope["statement"]["node_id"],
            expected_allocated=0,
            expected_currency_hash=asset.currency_hash, expected_serial=1)
        self.assertEqual(result["outcome"], statement.OK)
