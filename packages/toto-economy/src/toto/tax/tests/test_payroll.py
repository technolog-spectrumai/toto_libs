"""The payroll: the loop closes, and it closes exactly once.

Everything else in this app collects. This is the only thing on the platform
that pays a person, so the properties worth pinning are conservation (what left
the treasury is what arrived), idempotence (a double-fired beat pays once), and
honesty about who paid whom.
"""

from decimal import Decimal

from django.utils import timezone

from toto.assets.testing import LedgerTestCase as TestCase

from .. import payroll
from ..models import StipendPayment, StipendStatus
from .factories import make_gas_asset, make_user


def fund_treasury(asset, display):
    """Put money in the treasury the way a day of charges would.

    Straight into the holding rather than through a transfer: the point of
    these tests is what the payroll does with a funded treasury, and the
    collecting half is covered by the levy suite.
    """
    from toto.assets.models import AssetHolding, to_base_units

    base = to_base_units(Decimal(display), asset.decimals)
    holding, _ = AssetHolding.objects.get_or_create(
        asset=asset, account=payroll.treasury(),
        defaults={"balance_base_units": 0})
    holding.balance_base_units += base
    holding.save(update_fields=["balance_base_units"])
    return base


def make_station(name="Archivist", stipend="5", serves=None, holder=None):
    from toto.people.models import Person
    from toto.socialhub.models import Station

    if holder is None:
        user = make_user(name.lower().replace(" ", "-"))
        holder = Person.objects.create(user=user, display_name=name)
    return Station.objects.create(
        name=name, holder=holder, serves=serves, stipend=Decimal(stipend))


def balance(asset, account):
    from toto.assets.queries import get_asset_balance

    return get_asset_balance(asset, account)


class PayrollTests(TestCase):
    def setUp(self):
        self.asset = make_gas_asset()

    def test_a_held_office_is_paid_by_the_treasury(self):
        fund_treasury(self.asset, "100")
        station = make_station()

        summary = payroll.run_payroll()

        self.assertEqual(summary["paid"], 1)
        payment = StipendPayment.objects.get()
        self.assertEqual(payment.status, StipendStatus.PAID)
        # Who paid whom, stored rather than inferred.
        self.assertEqual(payment.payer_account, payroll.treasury())
        self.assertEqual(payment.payee_account.user, station.holder.user)
        self.assertIn("federal treasury",
                      payment.ledger_transaction.description)

    def test_the_money_actually_moves_and_conserves(self):
        funded = fund_treasury(self.asset, "100")
        station = make_station(stipend="5")

        payroll.run_payroll()

        payment = StipendPayment.objects.get()
        self.assertEqual(balance(self.asset, payroll.treasury()),
                         funded - payment.amount_base_units)
        self.assertEqual(balance(self.asset, payment.payee_account),
                         payment.amount_base_units)

    def test_running_twice_in_a_period_pays_once(self):
        funded = fund_treasury(self.asset, "100")
        make_station(stipend="5")

        payroll.run_payroll()
        second = payroll.run_payroll()

        self.assertEqual(StipendPayment.objects.count(), 1)
        self.assertEqual(second["created"], 0)
        payment = StipendPayment.objects.get()
        self.assertEqual(balance(self.asset, payroll.treasury()),
                         funded - payment.amount_base_units)

    def test_a_vacant_office_pays_nobody(self):
        from toto.socialhub.models import Station

        fund_treasury(self.asset, "100")
        Station.objects.create(name="Warden", stipend=Decimal("5"))

        summary = payroll.run_payroll()

        self.assertEqual(summary["created"], 0)
        self.assertEqual(StipendPayment.objects.count(), 0)

    def test_an_unpaid_office_creates_no_row(self):
        fund_treasury(self.asset, "100")
        make_station(stipend="0")

        payroll.run_payroll()

        self.assertEqual(StipendPayment.objects.count(), 0)

    def test_an_office_serving_a_community_is_still_paid_federally(self):
        from toto.socialhub.models import Community

        fund_treasury(self.asset, "100")
        guild = Community.objects.create(name="Weavers")
        make_station(name="Master of the Weavers", serves=guild)

        payroll.run_payroll()

        payment = StipendPayment.objects.get()
        self.assertEqual(payment.payer_account, payroll.treasury())
        # And no community account exists anywhere to have paid it.
        from toto.assets.models import LedgerAccount

        self.assertFalse(
            LedgerAccount.objects.filter(code__startswith="community-").exists())


class DryTreasuryTests(TestCase):
    """A salary that cannot be paid is still owed — the inverse of a levy."""

    def setUp(self):
        self.asset = make_gas_asset()

    def test_an_empty_treasury_leaves_the_payment_owed(self):
        make_station(stipend="5")

        summary = payroll.run_payroll()

        self.assertEqual(summary["unfunded"], 1)
        self.assertEqual(summary["paid"], 0)
        payment = StipendPayment.objects.get()
        self.assertEqual(payment.status, StipendStatus.PENDING)
        self.assertIn("last_error", payment.note)

    def test_the_next_run_pays_what_was_owed(self):
        make_station(stipend="5")
        payroll.run_payroll()          # unfunded

        fund_treasury(self.asset, "100")
        summary = payroll.run_payroll()

        self.assertEqual(summary["paid"], 1)
        self.assertEqual(StipendPayment.objects.get().status,
                         StipendStatus.PAID)
        # Still one row: it was owed, not re-owed.
        self.assertEqual(StipendPayment.objects.count(), 1)

    def test_an_empty_treasury_raises_nothing(self):
        make_station(stipend="5")
        summary = payroll.run_payroll()
        self.assertEqual(summary["paid"], 0)


class LoopTests(TestCase):
    """Collected, then paid back out. The whole point of the exercise."""

    def test_what_the_platform_collects_is_what_it_can_pay(self):
        from toto.assets.models import to_base_units
        from toto.assets.services.assets import transfer_asset
        from toto.tariffs.charge import _get_billing_account

        asset = make_gas_asset()
        payer = make_user("payer")
        # Stand in for a day of charges: a user pays the treasury.
        payer_account = _get_billing_account(payer)
        from toto.assets.models import AssetHolding

        AssetHolding.objects.create(
            asset=asset, account=payer_account,
            balance_base_units=to_base_units(Decimal("10"), asset.decimals))
        transfer_asset(asset=asset, sender_account=payer_account,
                       receiver_account=payroll.treasury(),
                       amount=Decimal("10"), reference="a-day-of-charges")

        make_station(stipend="10")
        payroll.run_payroll()

        # The treasury is empty again and the officer holds what was collected.
        self.assertEqual(balance(asset, payroll.treasury()), 0)
        payment = StipendPayment.objects.get()
        self.assertEqual(balance(asset, payment.payee_account),
                         to_base_units(Decimal("10"), asset.decimals))


class FeeBoardTests(TestCase):
    """The income board grew a spend side, because now there is one."""

    def test_the_board_shows_collected_paid_and_held(self):
        from toto.quota.feeboard import income_board

        asset = make_gas_asset()
        fund_treasury(asset, "100")
        make_station(stipend="30")

        payroll.run_payroll()
        board = income_board()

        self.assertEqual(board.paid_out, Decimal("30"))
        self.assertEqual(board.held, Decimal("70"))

    def test_an_unspent_treasury_reports_zero_paid(self):
        from toto.quota.feeboard import income_board

        asset = make_gas_asset()
        fund_treasury(asset, "100")

        board = income_board()

        self.assertEqual(board.paid_out, Decimal("0"))
        self.assertEqual(board.held, Decimal("100"))
