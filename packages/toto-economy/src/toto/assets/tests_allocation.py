"""Funding a branch: a transfer, never an increase.

The property under test everywhere here is conservation. An allocation moves
units; a drawdown moves units; a reassignment moves units back. At no point
does the total in existence change, because nothing on this platform can make
more of anything.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import override_settings

from toto.assets import allocation
from toto.assets.contracts import assign_contract, node_id
from toto.assets.issuer import NotTheMaster
from toto.assets.models import (AccountType, AssetHolding, CurrencyIssuer,
                                LedgerAccount)
from toto.assets.testing import LedgerTestCase as TestCase
from toto.mint.services import issue_asset


def _circulating(asset) -> int:
    """Every unit of this asset that exists anywhere in this database."""
    return int(AssetHolding.objects.filter(asset=asset).aggregate(
        total=Sum("balance_base_units"))["total"] or 0)


class AllocationConservesSupplyTests(TestCase):
    def setUp(self):
        self.asset = issue_asset(name="Assarion", unit_name="ASR",
                                 total_supply=Decimal("1000"), decimals=2,
                                 reason="Gas.")

    def test_allocating_moves_units_and_creates_none(self):
        before = _circulating(self.asset)

        allocation.allocate_to_branch(node="placidia", asset=self.asset,
                                      amount=Decimal("100"), reference="a1")

        self.assertEqual(_circulating(self.asset), before)
        self.assertEqual(_circulating(self.asset),
                         self.asset.max_supply_base_units)

    def test_the_master_knows_how_much_is_out_where(self):
        allocation.allocate_to_branch(node="placidia", asset=self.asset,
                                      amount=Decimal("100"), reference="a1")
        allocation.allocate_to_branch(node="delta", asset=self.asset,
                                      amount=Decimal("30"), reference="a2")

        placidia = allocation.position_account(node="placidia", asset=self.asset)
        delta = allocation.position_account(node="delta", asset=self.asset)
        self.assertEqual(
            AssetHolding.objects.get(asset=self.asset,
                                     account=placidia).balance_base_units,
            10000)
        self.assertEqual(
            AssetHolding.objects.get(asset=self.asset,
                                     account=delta).balance_base_units, 3000)

    def test_the_position_account_is_external(self):
        account = allocation.position_account(node="placidia", asset=self.asset)
        self.assertEqual(account.account_type, AccountType.EXTERNAL)

    def test_a_branch_cannot_allocate(self):
        CurrencyIssuer.objects.filter(is_self=True).update(
            private_key_encrypted=None)
        with self.assertRaises(NotTheMaster):
            allocation.allocate_to_branch(node="x", asset=self.asset,
                                          amount=Decimal("1"), reference="a3")

    def test_a_negative_allocation_is_refused(self):
        with self.assertRaises(ValidationError):
            allocation.allocate_to_branch(node="x", asset=self.asset,
                                          amount=Decimal("-1"), reference="a4")

    def test_allocating_more_than_the_reserve_holds_is_refused(self):
        # There is no overdraft anywhere: the reserve is the ceiling, and
        # double-entry is what enforces it.
        with self.assertRaises(ValidationError):
            allocation.allocate_to_branch(node="x", asset=self.asset,
                                          amount=Decimal("100000"),
                                          reference="a5")


class BranchSideTests(TestCase):
    def setUp(self):
        self.asset = issue_asset(name="Assarion", unit_name="ASR",
                                 total_supply=Decimal("1000"), decimals=2,
                                 reason="Gas.")
        assign_contract(node=node_id(), asset=self.asset)

    def test_receiving_credits_the_local_reserve(self):
        allocation.receive_allocation(asset=self.asset, amount=Decimal("50"),
                                      reference="r1")

        reserve = allocation.branch_reserve(asset=self.asset)
        self.assertEqual(
            AssetHolding.objects.get(asset=self.asset,
                                     account=reserve).balance_base_units, 5000)

    def test_an_uncontracted_asset_cannot_be_received(self):
        # The holdings guard: a branch may only hold what it was contracted
        # for, current or superseded. Anything else is not its business.
        stranger = issue_asset(name="Other", unit_name="OTH",
                               total_supply=Decimal("10"), decimals=0,
                               reason="Demo.")
        with self.assertRaises(ValidationError) as caught:
            allocation.receive_allocation(asset=stranger, amount=Decimal("1"),
                                          reference="r2")
        self.assertIn("never been contracted",
                      "; ".join(caught.exception.messages))

    def test_allocated_and_drawn_are_readable_by_subtraction(self):
        allocation.receive_allocation(asset=self.asset, amount=Decimal("50"),
                                      reference="r1")
        user = LedgerAccount.objects.create(code="u1", name="u1",
                                            account_type=AccountType.USER)
        from toto.assets.services.assets import transfer_asset
        transfer_asset(asset=self.asset,
                       sender_account=allocation.branch_reserve(asset=self.asset),
                       receiver_account=user, amount=Decimal("20"),
                       reference="grant-1")

        self.assertEqual(allocation.allocated_base_units(asset=self.asset), 5000)
        self.assertEqual(allocation.drawn_base_units(asset=self.asset), 2000)

    def test_the_reserve_is_the_ceiling(self):
        from toto.assets.services.assets import transfer_asset

        allocation.receive_allocation(asset=self.asset, amount=Decimal("10"),
                                      reference="r1")
        user = LedgerAccount.objects.create(code="u1", name="u1",
                                            account_type=AccountType.USER)
        # No rule says "you may not exceed your allocation" — double-entry
        # simply will not let it happen.
        with self.assertRaises(ValidationError):
            transfer_asset(
                asset=self.asset,
                sender_account=allocation.branch_reserve(asset=self.asset),
                receiver_account=user, amount=Decimal("11"), reference="over")


class ReassignmentTests(TestCase):
    def setUp(self):
        self.old = issue_asset(name="Old", unit_name="OLD",
                               total_supply=Decimal("1000"), decimals=2,
                               reason="First currency.")
        self.new = issue_asset(name="New", unit_name="NEW",
                               total_supply=Decimal("1000"), decimals=2,
                               reason="Second currency.")
        assign_contract(node=node_id(), asset=self.old)
        allocation.receive_allocation(asset=self.old, amount=Decimal("100"),
                                      reference="r1")
        self.user = LedgerAccount.objects.create(code="u1", name="u1",
                                                 account_type=AccountType.USER)
        from toto.assets.services.assets import transfer_asset
        transfer_asset(asset=self.old,
                       sender_account=allocation.branch_reserve(asset=self.old),
                       receiver_account=self.user, amount=Decimal("30"),
                       reference="grant-1")

    def test_the_unspent_reserve_goes_back(self):
        allocation.return_unspent(asset=self.old, reference="ret-1")

        reserve = allocation.branch_reserve(asset=self.old)
        self.assertEqual(
            AssetHolding.objects.get(asset=self.old,
                                     account=reserve).balance_base_units, 0)

    def test_what_users_already_hold_stays_theirs(self):
        allocation.return_unspent(asset=self.old, reference="ret-1")

        self.assertEqual(
            AssetHolding.objects.get(asset=self.old,
                                     account=self.user).balance_base_units,
            3000)

    def test_the_old_currency_is_still_spendable_after_the_switch(self):
        from toto.assets.contracts import may_hold

        assign_contract(node=node_id(), asset=self.new)
        allocation.return_unspent(asset=self.old, reference="ret-1")

        # Legacy: held and spendable, but nothing new prices in it.
        self.assertTrue(may_hold(self.old))
        from toto.tariffs.rate_card import gas_asset
        self.assertEqual(gas_asset(), self.new)

    def test_the_whole_ceremony_conserves_supply(self):
        before = _circulating(self.old)
        assign_contract(node=node_id(), asset=self.new)
        allocation.return_unspent(asset=self.old, reference="ret-1")

        self.assertEqual(_circulating(self.old), before)

    def test_returning_an_empty_reserve_is_a_no_op(self):
        allocation.return_unspent(asset=self.old, reference="ret-1")
        self.assertIsNone(allocation.return_unspent(asset=self.old,
                                                    reference="ret-2"))
