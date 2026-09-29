"""The member-facing charge helpers beyond the happy path: who pays (the
billing account), what a post-time shortfall becomes, a stipend paid the
other way (``credit_user``), a refund's guards, the refusal that must never
become a 500, and which tariff binds when several could.
"""

from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from toto.assets.models import (AccountType, AssetHolding, LedgerAccount,
                                LedgerTransaction)
from toto.assets.prepaid import get_or_create_prepaid_account
from toto.assets.testing import LedgerTestCase as TestCase
from toto.assets.testing import make_asset
from toto.tariffs import charge as charging
from toto.tariffs.charge import (InsufficientBalanceError, MonetaryAuthorityUnreachable,
                                 charge_user, check_user_can_act, credit_user,
                                 get_tariff_for_user, refund_usage_record)
from toto.tariffs.models import (BillingMetric, Tariff, TariffItem, TariffStatus,
                                 UsageRecord, UsageStatus)
from toto.tariffs.rate_card import revenue_account

User = get_user_model()


def account(code, **extra):
    return LedgerAccount.objects.create(code=code, name=code,
                                        active=extra.pop("active", True),
                                        account_type=extra.pop("account_type", AccountType.SYSTEM),
                                        **extra)


def fund(account_, asset, base_units):
    holding, _ = AssetHolding.objects.get_or_create(account=account_, asset=asset)
    holding.balance_base_units = base_units
    holding.save()
    return holding


def balance(account_, asset):
    holding = AssetHolding.objects.filter(account=account_, asset=asset).first()
    return holding.balance_base_units if holding else 0


class ChargeTestCase(TestCase):
    """One asset with two decimals, one tariff pricing ``test.op`` at 1.00."""

    def setUp(self):
        self.asset = make_asset(unit_name="CRD", decimals=2, active=True)
        self.revenue = account("REV-CRD")
        self.tariff = self.make_tariff("CRD-T", app_label="crdapp")
        self.ada = User.objects.create_user("ada", password="pw")
        self.prepaid, _ = get_or_create_prepaid_account(self.ada)

    def make_tariff(self, code, *, app_label, price="1", status=TariffStatus.ACTIVE,
                    source_type="", metric="test.op", receiving=None):
        tariff = Tariff.objects.create(name=code, code=code, status=status,
                                       source_type=source_type)
        found, _ = BillingMetric.objects.get_or_create(
            code=metric, defaults={"label": metric, "active": True, "app_label": app_label})
        TariffItem.objects.create(tariff=tariff, name=metric, metric=found,
                                  charged_asset=self.asset,
                                  price_per_unit_display=Decimal(price),
                                  receiving_account=receiving or self.revenue, active=True)
        return tariff


class ErrorShapeTests(TestCase):
    def test_an_unreachable_master_is_a_503_with_a_plain_sentence(self):
        exc = MonetaryAuthorityUnreachable()
        self.assertEqual(exc.status_code, 503)
        self.assertIn("cannot be reached", str(exc))
        self.assertIn("Billing and spending are unaffected", str(exc))

    def test_an_unreachable_master_keeps_a_given_detail(self):
        self.assertEqual(str(MonetaryAuthorityUnreachable("try later")), "try later")

    def test_it_is_not_a_kind_of_insufficient_balance(self):
        """An outage must never be read as "no price" or "cannot pay"."""
        self.assertFalse(issubclass(MonetaryAuthorityUnreachable, InsufficientBalanceError))

    def test_a_detail_replaces_the_generic_insufficient_sentence(self):
        exc = InsufficientBalanceError("RED", 500, 100, asset_decimals=2,
                                       detail="Not enough compute mana.")
        self.assertEqual(str(exc), "Not enough compute mana.")
        self.assertEqual(exc.shortfall_display, Decimal("4"))


class BillingAccountTests(ChargeTestCase):
    def test_a_member_with_no_priority_account_pays_from_prepaid(self):
        self.assertEqual(charging._get_billing_account(self.ada), self.prepaid)

    def test_the_highest_priority_active_account_pays(self):
        low = account("ADA-LOW", user=self.ada, user_priority=1,
                      account_type=AccountType.USER)
        high = account("ADA-HIGH", user=self.ada, user_priority=5,
                       account_type=AccountType.USER)
        account("ADA-OFF", user=self.ada, user_priority=9, account_type=AccountType.USER,
                active=False)
        self.assertEqual(charging._get_billing_account(self.ada), high)
        self.assertNotEqual(charging._get_billing_account(self.ada), low)

    def test_a_charge_debits_the_priority_account_not_prepaid(self):
        main = account("ADA-MAIN", user=self.ada, user_priority=3, account_type=AccountType.USER)
        fund(main, self.asset, 500)
        fund(self.prepaid, self.asset, 500)
        charge_user(self.ada, self.tariff, "test.op", 2)
        self.assertEqual((balance(main, self.asset), balance(self.prepaid, self.asset)), (300, 500))


class PostTimeShortfallTests(ChargeTestCase):
    def test_a_shortfall_found_at_post_time_is_a_402_not_a_bare_value_error(self):
        fund(self.prepaid, self.asset, 50)
        with self.assertRaises(InsufficientBalanceError) as caught:
            charge_user(self.ada, self.tariff, "test.op", 1)
        self.assertEqual(caught.exception.status_code, 402)
        record = UsageRecord.objects.get(payer_account=self.prepaid)
        self.assertEqual(record.status, UsageStatus.FAILED)
        self.assertIn("short by", record.error_message)
        self.assertEqual(balance(self.prepaid, self.asset), 50)

    def test_any_other_value_error_is_not_dressed_up_as_a_shortfall(self):
        with mock.patch("toto.tariffs.services.record_and_post_usage",
                        side_effect=ValueError("Cannot post a UsageRecord in status 'reversed'.")):
            with self.assertRaises(ValueError) as caught:
                charge_user(self.ada, self.tariff, "test.op", 1)
        self.assertNotIsInstance(caught.exception, InsufficientBalanceError)

    def test_the_default_description_names_the_metric_and_quantity(self):
        fund(self.prepaid, self.asset, 500)
        _record, tx = charge_user(self.ada, self.tariff, "test.op", 3)
        self.assertEqual(tx.description, "test.op × 3")


class RefusalWordingTests(ChargeTestCase):
    def test_a_failing_mana_sentence_falls_back_to_the_plain_refusal(self):
        """The nicer wording is optional; the refusal is not."""
        with mock.patch("toto.mana.services.explain_shortfall",
                        side_effect=RuntimeError("pools table gone")):
            with self.assertRaises(InsufficientBalanceError) as caught:
                check_user_can_act(self.ada, self.tariff, "test.op", 1)
        self.assertTrue(str(caught.exception).startswith("Insufficient CRD"))
        self.assertEqual((caught.exception.needed_base_units, caught.exception.have_base_units),
                         (100, 0))

    def test_a_host_without_mana_uses_the_plain_refusal(self):
        from django.apps import apps

        real = apps.is_installed
        with mock.patch.object(apps, "is_installed",
                               side_effect=lambda name: name != "toto.mana" and real(name)):
            self.assertEqual(charging._mana_detail(self.ada, self.asset, 100, 0), "")

    def test_a_currency_price_gets_no_mana_sentence(self):
        self.assertEqual(charging._mana_detail(self.ada, self.asset, 100, 0), "")


class CreditTests(ChargeTestCase):
    """A stipend: the treasury pays the member, through the same rate card."""

    def setUp(self):
        super().setUp()
        self.treasury = revenue_account()
        self.stipend = self.make_tariff("STIPEND", app_label="stipends", metric="test.stipend",
                                        price="2", receiving=self.treasury)

    def test_the_treasury_pays_the_member_the_priced_amount(self):
        fund(self.treasury, self.asset, 1000)
        tx = credit_user(self.ada, self.stipend, "test.stipend", 3, reference="stipend-1")
        self.assertIsNotNone(tx)
        self.assertEqual((balance(self.prepaid, self.asset), balance(self.treasury, self.asset)),
                         (600, 400))
        self.assertEqual(tx.metadata["kind"], "credit")

    def test_the_same_reference_pays_once(self):
        fund(self.treasury, self.asset, 1000)
        first = credit_user(self.ada, self.stipend, "test.stipend", 1, reference="stipend-2026-09")
        again = credit_user(self.ada, self.stipend, "test.stipend", 1, reference="stipend-2026-09")
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(balance(self.prepaid, self.asset), 200)

    def test_without_a_reference_one_is_built_from_the_source(self):
        fund(self.treasury, self.asset, 1000)
        tx = credit_user(self.ada, self.stipend, "test.stipend", 1,
                         source_type="plans.Stipend", source_id=7)
        self.assertEqual(tx.reference, "credit-test.stipend-plans.Stipend-7")

    def test_an_unpriced_metric_pays_nothing_and_is_not_a_failure(self):
        before = LedgerTransaction.objects.count()
        self.assertIsNone(credit_user(self.ada, self.stipend, "no.such.metric", 1))
        self.assertEqual(LedgerTransaction.objects.count(), before)

    def test_a_price_of_zero_pays_nothing(self):
        TariffItem.objects.filter(tariff=self.stipend).update(price_per_unit_base_units=0)
        self.assertIsNone(credit_user(self.ada, self.stipend, "test.stipend", 5))

    def test_an_empty_treasury_is_left_to_the_caller(self):
        """The caller holds the row that records the debt, so it must hear."""
        with self.assertRaises(ValidationError):
            credit_user(self.ada, self.stipend, "test.stipend", 1, reference="dry")
        self.assertEqual(balance(self.prepaid, self.asset), 0)


class RefundTests(ChargeTestCase):
    def setUp(self):
        super().setUp()
        fund(self.prepaid, self.asset, 1000)
        self.record, self.tx = charge_user(self.ada, self.tariff, "test.op", 2)

    def test_a_refund_restores_the_payer_and_marks_the_record_reversed(self):
        reversal = refund_usage_record(self.record, reference="refund-custom")
        self.assertEqual(reversal.reference, "refund-custom")
        self.assertEqual(reversal.reversed_transaction, self.tx)
        self.assertEqual(balance(self.prepaid, self.asset), 1000)
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, UsageStatus.REVERSED)

    def test_a_second_refund_gives_nothing_back(self):
        refund_usage_record(self.record)
        self.assertIsNone(refund_usage_record(self.record))
        self.assertEqual(balance(self.prepaid, self.asset), 1000)

    def test_nothing_to_refund(self):
        self.assertIsNone(refund_usage_record(None))

    def test_a_record_that_never_posted_is_not_refunded(self):
        self.record.status = UsageStatus.FAILED
        self.assertIsNone(refund_usage_record(self.record))
        self.assertEqual(balance(self.prepaid, self.asset), 800)

    def test_a_credit_the_platform_already_spent_is_not_clawed_back(self):
        """``reverse_transaction`` checks no balance; this guard is the only
        thing between a refund and a negative treasury."""
        fund(self.revenue, self.asset, 50)                 # spent 150 of the 200
        with self.assertLogs("toto.tariffs", level="WARNING"):
            self.assertIsNone(refund_usage_record(self.record))
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, UsageStatus.POSTED)
        self.assertEqual(balance(self.revenue, self.asset), 50)
        self.assertEqual(balance(self.prepaid, self.asset), 800)

    def test_a_reversal_the_ledger_refuses_is_logged_and_declined(self):
        with mock.patch("toto.assets.services.assets.reverse_transaction",
                        side_effect=ValidationError("frozen")), \
                self.assertLogs("toto.tariffs", level="WARNING"):
            self.assertIsNone(refund_usage_record(self.record))
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, UsageStatus.POSTED)


class TariffResolutionTests(ChargeTestCase):
    def test_the_platform_default_wins_over_a_community_tariff(self):
        community = self.make_tariff("COMMUNITY-T", app_label="resapp", metric="res.op",
                                     source_type="socialhub.Community")
        platform = self.make_tariff("PLATFORM-T", app_label="resapp", metric="res.op")
        self.assertEqual(get_tariff_for_user(self.ada, "resapp"), platform)
        self.assertNotEqual(get_tariff_for_user(self.ada, "resapp"), community)

    def test_any_active_tariff_is_the_last_resort(self):
        only = self.make_tariff("ONLY-T", app_label="lastapp", metric="last.op",
                                source_type="socialhub.Community")
        self.assertEqual(get_tariff_for_user(self.ada, "lastapp"), only)

    def test_a_draft_or_paused_tariff_never_binds(self):
        self.make_tariff("DRAFT-T", app_label="draftapp", metric="draft.op",
                         status=TariffStatus.DRAFT)
        self.make_tariff("PAUSED-T", app_label="draftapp", metric="draft.op2",
                         status=TariffStatus.PAUSED)
        self.assertIsNone(get_tariff_for_user(self.ada, "draftapp"))

    def test_a_tariff_whose_only_item_is_inactive_does_not_bind(self):
        tariff = self.make_tariff("OFF-T", app_label="offapp", metric="off.op")
        tariff.items.update(active=False)
        self.assertIsNone(get_tariff_for_user(self.ada, "offapp"))
