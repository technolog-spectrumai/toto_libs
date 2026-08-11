"""The anti-cheat: a branch's account of itself, checked by the master."""

from decimal import Decimal

from django.test import override_settings

from toto.assets import allocation, statement
from toto.assets.contracts import assign_contract, node_id
from toto.assets.models import AccountType, LedgerAccount, PlatformKey
from toto.assets.services.assets import transfer_asset
from toto.assets.testing import LedgerTestCase as TestCase
from toto.mint.services import issue_asset
from toto.tariffs.charge import MonetaryAuthorityUnreachable


class UnreachableIsNotFreeTests(TestCase):
    """The single most important distinction in the whole design."""

    def test_it_is_an_exception_with_its_own_status_code(self):
        exc = MonetaryAuthorityUnreachable()
        self.assertEqual(exc.status_code, 503)

    def test_it_is_not_the_payment_code_and_not_the_quota_code(self):
        from toto.tariffs.charge import InsufficientBalanceError

        # 503 "cannot answer", 402 "cannot pay", 429 "over your cap". Three
        # different facts; a caller reading exc.status_code tells them apart
        # without knowing anything about the economy.
        self.assertNotEqual(MonetaryAuthorityUnreachable.status_code,
                            InsufficientBalanceError.status_code)
        self.assertNotEqual(MonetaryAuthorityUnreachable.status_code, 429)

    def test_the_message_says_billing_is_unaffected(self):
        # Because it is: billing is local and makes no network call at all.
        self.assertIn("local", str(MonetaryAuthorityUnreachable()))

    def test_the_billing_facade_is_untouched_by_any_of_this(self):
        # An unpriced metric and an unbilled host both mean FREE, and nothing
        # here may add a fourth meaning to that sentinel.
        import inspect

        from toto.quota import charge

        source = inspect.getsource(charge)
        self.assertNotIn("Unreachable", source)
        self.assertNotIn("contractual_asset", source)


class StatementTests(TestCase):
    def setUp(self):
        self.asset = issue_asset(name="Assarion", unit_name="ASR",
                                 total_supply=Decimal("1000"), decimals=2,
                                 reason="Gas.")
        assign_contract(node=node_id(), asset=self.asset)
        allocation.receive_allocation(asset=self.asset, amount=Decimal("100"),
                                      reference="r1")
        user = LedgerAccount.objects.create(code="u1", name="u1",
                                            account_type=AccountType.USER)
        transfer_asset(
            asset=self.asset,
            sender_account=allocation.branch_reserve(asset=self.asset),
            receiver_account=user, amount=Decimal("40"), reference="grant-1")

    def _envelope(self):
        return statement.sign_statement(statement.build_statement())

    def _verify(self, envelope, **overrides):
        kwargs = dict(expected_node=node_id(), expected_allocated=10000,
                      expected_currency_hash=self.asset.currency_hash,
                      expected_serial=1)
        kwargs.update(overrides)
        return statement.verify_statement(envelope, **kwargs)

    def test_a_truthful_statement_verifies(self):
        result = self._verify(self._envelope())

        self.assertEqual(result["outcome"], statement.OK)
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["statement"]["drawn_base_units"], 4000)
        self.assertEqual(result["statement"]["allocated_base_units"], 10000)

    def test_the_statement_carries_the_chain_head_as_evidence(self):
        envelope = self._envelope()
        self.assertTrue(envelope["statement"]["chain_head"])

    def test_it_carries_no_user_identities(self):
        # Account codes only — the same privacy rule the clearing wire states.
        blob = str(self._envelope())
        self.assertNotIn("u1@", blob)
        self.assertNotIn("password", blob)

    def test_a_tampered_statement_is_unverifiable_not_merely_wrong(self):
        envelope = self._envelope()
        envelope["statement"]["drawn_base_units"] = 1

        result = self._verify(envelope)
        self.assertEqual(result["outcome"], statement.UNVERIFIABLE)

    def test_over_drawing_is_a_discrepancy(self):
        # Signed honestly, but the arithmetic does not add up — a branch whose
        # code was changed. This is the case the check exists for.
        forged = statement.build_statement()
        forged["drawn_base_units"] = 999999
        envelope = statement.sign_statement(forged)

        result = self._verify(envelope)
        self.assertEqual(result["outcome"], statement.DISCREPANCY)
        self.assertTrue(any("drew" in f for f in result["findings"]))

    def test_claiming_more_allocation_than_was_sent_is_a_discrepancy(self):
        forged = statement.build_statement()
        forged["allocated_base_units"] = 500000
        envelope = statement.sign_statement(forged)

        result = self._verify(envelope)
        self.assertEqual(result["outcome"], statement.DISCREPANCY)

    def test_a_statement_about_another_currency_is_a_discrepancy(self):
        other = issue_asset(name="Other", unit_name="OTH",
                            total_supply=Decimal("1"), decimals=0,
                            reason="Demo.")
        result = self._verify(self._envelope(),
                              expected_currency_hash=other.currency_hash)
        self.assertEqual(result["outcome"], statement.DISCREPANCY)

    def test_a_stale_contract_serial_is_a_discrepancy(self):
        result = self._verify(self._envelope(), expected_serial=7)
        self.assertEqual(result["outcome"], statement.DISCREPANCY)

    def test_verification_changes_nothing(self):
        # No automatic punishment: a discrepancy is recorded and flagged, and
        # what happens next is an administrator's decision.
        from toto.assets.models import CurrencyContract

        forged = statement.build_statement()
        forged["drawn_base_units"] = 999999
        self._verify(statement.sign_statement(forged))

        self.assertTrue(CurrencyContract.objects.filter(
            is_local=True, superseded_at__isnull=True).exists())


class PlatformKeyTests(TestCase):
    def test_the_platform_key_is_not_the_issuer_key(self):
        # A branch can attest to its books without ever being able to issue.
        from toto.assets.issuer import local_issuer

        key = PlatformKey.objects.ensure_local()
        self.assertNotEqual(key.fingerprint, local_issuer().fingerprint)

    def test_only_one_local_platform_key(self):
        first = PlatformKey.objects.ensure_local()
        again = PlatformKey.objects.ensure_local()
        self.assertEqual(first.pk, again.pk)

    def test_a_statement_signature_never_verifies_as_a_genesis_one(self):
        # Different context strings: a signature over one kind of thing can
        # never be replayed as another.
        from toto.assets.currency_hash import GENESIS_CONTEXT

        self.assertNotEqual(statement.STATEMENT_CONTEXT, GENESIS_CONTEXT)
