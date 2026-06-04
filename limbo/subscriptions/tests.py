from __future__ import annotations

from decimal import Decimal
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_user(username):
    return User.objects.create_user(username=username, password="pass")


def _make_community(name="Test Community"):
    from toto.socialhub.models import Community
    return Community.objects.create(name=name)


def _make_person(username="test-person"):
    from toto.people.models import Person
    user = _make_user(username)
    return Person.objects.create(display_name=username, user=user)


def _make_ledger_account(code):
    from toto.assets.models import LedgerAccount
    return LedgerAccount.objects.create(code=code, name=code, account_type="user", active=True)


def _make_asset(code, reserve):
    from toto.assets.services.assets import create_asset
    return create_asset(
        name=code, unit_name=code, total_supply=Decimal("10000"),
        decimals=2, reserve_account=reserve, reference=f"create-{code.lower()}",
    )


def _make_plan(community, code="test-plan", kind="custom", status="active"):
    from toto.subscriptions.models import SubscriptionPlan
    return SubscriptionPlan.objects.create(
        community=community,
        name=code,
        slug=code,
        code=code,
        kind=kind,
        status=status,
    )


def _make_price(plan, asset, receiving_account, amount=1000, interval="monthly"):
    from toto.subscriptions.models import SubscriptionPrice
    return SubscriptionPrice.objects.create(
        plan=plan,
        name="Monthly",
        code="monthly",
        asset=asset,
        amount_base_units=amount,
        interval=interval,
        interval_count=1,
        receiving_account=receiving_account,
    )


def _make_feature(plan, code="feature-a", kind="allowance", quantity=Decimal("100")):
    from toto.subscriptions.models import SubscriptionFeature
    return SubscriptionFeature.objects.create(
        plan=plan,
        code=code,
        name=code,
        kind=kind,
        quantity=quantity,
        unit="units",
        hard_limit=False,
        active=True,
    )


def _make_customer(community, person, billing_account, asset):
    from toto.subscriptions.services import get_or_create_customer
    return get_or_create_customer(
        community=community,
        person=person,
        billing_account=billing_account,
        default_asset=asset,
    )


# ---------------------------------------------------------------------------
# Model validation tests
# ---------------------------------------------------------------------------

class BillingIntervalTests(TestCase):
    def test_choices_present(self):
        from toto.subscriptions.models import BillingInterval
        values = [c[0] for c in BillingInterval.choices]
        for v in ["once", "daily", "weekly", "monthly", "quarterly", "yearly", "custom"]:
            self.assertIn(v, values)


class SubscriptionPlanModelTests(TestCase):
    def setUp(self):
        self.community = _make_community("plan-community")

    def test_slug_auto_generated(self):
        from toto.subscriptions.models import SubscriptionPlan
        plan = SubscriptionPlan.objects.create(
            community=self.community,
            name="My Cool Plan",
            code="my-cool-plan",
            status=SubscriptionPlan.Status.DRAFT,
        )
        self.assertEqual(plan.slug, "my-cool-plan")

    def test_duplicate_code_same_community_rejected(self):
        from django.db import IntegrityError
        from toto.subscriptions.models import SubscriptionPlan
        SubscriptionPlan.objects.create(community=self.community, name="P1", slug="p1", code="dup")
        with self.assertRaises(IntegrityError):
            SubscriptionPlan.objects.create(community=self.community, name="P2", slug="p2", code="dup")

    def test_same_code_different_community_allowed(self):
        from toto.subscriptions.models import SubscriptionPlan
        other = _make_community("other-community")
        SubscriptionPlan.objects.create(community=self.community, name="P1", slug="p1", code="shared")
        plan2 = SubscriptionPlan.objects.create(community=other, name="P2", slug="p2", code="shared")
        self.assertIsNotNone(plan2.pk)


class SubscriptionPriceModelTests(TestCase):
    def setUp(self):
        self.community = _make_community("price-community")
        reserve = _make_ledger_account("price-reserve")
        receiving = _make_ledger_account("price-receiving")
        self.asset = _make_asset("PRT", reserve)
        self.plan = _make_plan(self.community, "price-plan")
        self.receiving = receiving

    def test_once_interval_with_count_gt_1_fails_clean(self):
        from toto.subscriptions.models import SubscriptionPrice, BillingInterval
        price = SubscriptionPrice(
            plan=self.plan,
            name="Bad",
            code="bad",
            asset=self.asset,
            amount_base_units=100,
            interval=BillingInterval.ONCE,
            interval_count=2,
            receiving_account=self.receiving,
        )
        with self.assertRaises(ValidationError):
            price.clean()

    def test_negative_amount_rejected_by_constraint(self):
        from toto.subscriptions.models import SubscriptionPrice
        price = SubscriptionPrice(
            plan=self.plan,
            name="Neg",
            code="neg",
            asset=self.asset,
            amount_base_units=-1,
            receiving_account=self.receiving,
        )
        with self.assertRaises(Exception):
            price.validate_constraints()


class SubscriptionModelTests(TestCase):
    def setUp(self):
        self.community = _make_community("sub-model-community")
        reserve = _make_ledger_account("sm-reserve")
        receiving = _make_ledger_account("sm-receiving")
        billing = _make_ledger_account("sm-billing")
        self.asset = _make_asset("SMT", reserve)
        self.plan = _make_plan(self.community, "sm-plan")
        self.price = _make_price(self.plan, self.asset, receiving)
        self.person = _make_person("sm-person")
        self.customer = _make_customer(self.community, self.person, billing, self.asset)

    def test_period_end_before_start_rejected(self):
        from toto.subscriptions.models import Subscription
        now = timezone.now()
        sub = Subscription(
            customer=self.customer,
            plan=self.plan,
            price=self.price,
            current_period_start=now,
            current_period_end=now - timedelta(seconds=1),
        )
        with self.assertRaises(ValidationError):
            sub.clean()

    def test_price_must_belong_to_plan(self):
        from toto.subscriptions.models import Subscription, SubscriptionPlan, SubscriptionPrice
        other_plan = SubscriptionPlan.objects.create(
            community=self.community, name="other", slug="other", code="other",
        )
        receiving2 = _make_ledger_account("sm-receiving2")
        other_price = SubscriptionPrice.objects.create(
            plan=other_plan, name="Other", code="monthly",
            asset=self.asset, amount_base_units=100, receiving_account=receiving2,
        )
        now = timezone.now()
        sub = Subscription(
            customer=self.customer,
            plan=self.plan,
            price=other_price,
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
        with self.assertRaises(ValidationError):
            sub.clean()


# ---------------------------------------------------------------------------
# Period helper tests
# ---------------------------------------------------------------------------

class PeriodEndForTests(TestCase):
    def _dt(self, year=2025, month=1, day=1):
        from django.utils.timezone import make_aware
        from datetime import datetime
        return make_aware(datetime(year, month, day, 12, 0, 0))

    def test_daily(self):
        from toto.subscriptions.services import period_end_for
        start = self._dt(2025, 1, 1)
        end = period_end_for(start, "daily", 7)
        self.assertEqual(end, start + timedelta(days=7))

    def test_weekly(self):
        from toto.subscriptions.services import period_end_for
        start = self._dt(2025, 1, 1)
        end = period_end_for(start, "weekly", 2)
        self.assertEqual(end, start + timedelta(weeks=2))

    def test_monthly(self):
        from toto.subscriptions.services import period_end_for
        start = self._dt(2025, 1, 15)
        end = period_end_for(start, "monthly", 1)
        self.assertEqual(end.month, 2)
        self.assertEqual(end.day, 15)

    def test_quarterly(self):
        from toto.subscriptions.services import period_end_for
        start = self._dt(2025, 1, 1)
        end = period_end_for(start, "quarterly", 1)
        self.assertEqual(end.month, 4)

    def test_yearly(self):
        from toto.subscriptions.services import period_end_for
        start = self._dt(2025, 3, 1)
        end = period_end_for(start, "yearly", 1)
        self.assertEqual(end.year, 2026)
        self.assertEqual(end.month, 3)


# ---------------------------------------------------------------------------
# Service: create_subscription
# ---------------------------------------------------------------------------

class CreateSubscriptionTests(TestCase):
    def setUp(self):
        self.community = _make_community("create-sub-community")
        reserve = _make_ledger_account("cs-reserve")
        receiving = _make_ledger_account("cs-receiving")
        billing = _make_ledger_account("cs-billing")
        self.asset = _make_asset("CST", reserve)
        self.plan = _make_plan(self.community, "cs-plan")
        self.price = _make_price(self.plan, self.asset, receiving)
        self.person = _make_person("cs-person")
        self.customer = _make_customer(self.community, self.person, billing, self.asset)

    def test_creates_subscription(self):
        from toto.subscriptions.services import create_subscription
        from toto.subscriptions.models import Subscription
        sub = create_subscription(
            customer=self.customer,
            plan=self.plan,
            price=self.price,
            invoice_now=False,
        )
        self.assertIsNotNone(sub.pk)
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)

    def test_creates_invoice_by_default(self):
        from toto.subscriptions.services import create_subscription
        from toto.subscriptions.models import SubscriptionInvoice
        sub = create_subscription(customer=self.customer, plan=self.plan, price=self.price, invoice_now=True)
        self.assertEqual(sub.invoices.count(), 1)
        invoice = sub.invoices.first()
        self.assertEqual(invoice.status, SubscriptionInvoice.Status.OPEN)

    def test_trial_plan_sets_trialing_status(self):
        from toto.subscriptions.services import create_subscription
        from toto.subscriptions.models import Subscription
        self.plan.trial_days = 14
        self.plan.save()
        sub = create_subscription(customer=self.customer, plan=self.plan, price=self.price, invoice_now=False)
        self.assertEqual(sub.status, Subscription.Status.TRIALING)
        self.assertIsNotNone(sub.trial_ends_at)

    def test_creates_entitlements_for_features(self):
        from toto.subscriptions.services import create_subscription
        _make_feature(self.plan, code="feat-a", kind="entitlement", quantity=None)
        sub = create_subscription(customer=self.customer, plan=self.plan, price=self.price, invoice_now=False)
        self.assertEqual(sub.entitlements.count(), 1)

    def test_creates_allowances_for_metered_features(self):
        from toto.subscriptions.services import create_subscription
        _make_feature(self.plan, code="tokens", kind="allowance", quantity=Decimal("1000"))
        sub = create_subscription(customer=self.customer, plan=self.plan, price=self.price, invoice_now=False)
        self.assertEqual(sub.allowances.count(), 1)
        allowance = sub.allowances.first()
        self.assertEqual(allowance.included_quantity, Decimal("1000"))

    def test_mismatched_plan_and_price_raises(self):
        from toto.subscriptions.services import create_subscription
        from toto.subscriptions.models import SubscriptionPlan, SubscriptionPrice
        other_plan = SubscriptionPlan.objects.create(
            community=self.community, name="other", slug="other", code="other",
        )
        receiving2 = _make_ledger_account("cs-receiving2")
        other_price = SubscriptionPrice.objects.create(
            plan=other_plan, name="Other", code="monthly",
            asset=self.asset, amount_base_units=100, receiving_account=receiving2,
        )
        with self.assertRaises(ValidationError):
            create_subscription(customer=self.customer, plan=self.plan, price=other_price, invoice_now=False)


# ---------------------------------------------------------------------------
# Service: cancel_subscription
# ---------------------------------------------------------------------------

class CancelSubscriptionTests(TestCase):
    def setUp(self):
        self.community = _make_community("cancel-community")
        reserve = _make_ledger_account("can-reserve")
        receiving = _make_ledger_account("can-receiving")
        billing = _make_ledger_account("can-billing")
        self.asset = _make_asset("CANT", reserve)
        self.plan = _make_plan(self.community, "can-plan")
        self.price = _make_price(self.plan, self.asset, receiving)
        self.person = _make_person("can-person")
        self.customer = _make_customer(self.community, self.person, billing, self.asset)

    def _sub(self):
        from toto.subscriptions.services import create_subscription
        return create_subscription(customer=self.customer, plan=self.plan, price=self.price, invoice_now=False)

    def test_cancel_at_period_end_sets_flag(self):
        from toto.subscriptions.services import cancel_subscription
        from toto.subscriptions.models import Subscription
        sub = self._sub()
        cancel_subscription(sub, at_period_end=True)
        sub.refresh_from_db()
        self.assertTrue(sub.cancel_at_period_end)
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)

    def test_cancel_immediately_changes_status(self):
        from toto.subscriptions.services import cancel_subscription
        from toto.subscriptions.models import Subscription
        sub = self._sub()
        cancel_subscription(sub, at_period_end=False)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.CANCELLED)
        self.assertIsNotNone(sub.cancelled_at)
        self.assertIsNotNone(sub.ended_at)

    def test_cancel_immediately_revokes_entitlements(self):
        from toto.subscriptions.services import cancel_subscription
        from toto.subscriptions.models import SubscriptionEntitlement
        _make_feature(self.plan, code="ent-x", kind="entitlement", quantity=None)
        sub = self._sub()
        cancel_subscription(sub, at_period_end=False)
        self.assertTrue(
            sub.entitlements.filter(status=SubscriptionEntitlement.Status.REVOKED).exists()
        )


# ---------------------------------------------------------------------------
# Service: renew_subscription
# ---------------------------------------------------------------------------

class RenewSubscriptionTests(TestCase):
    def setUp(self):
        self.community = _make_community("renew-community")
        reserve = _make_ledger_account("ren-reserve")
        receiving = _make_ledger_account("ren-receiving")
        billing = _make_ledger_account("ren-billing")
        self.asset = _make_asset("RENT", reserve)
        self.plan = _make_plan(self.community, "ren-plan")
        self.price = _make_price(self.plan, self.asset, receiving)
        self.person = _make_person("ren-person")
        self.customer = _make_customer(self.community, self.person, billing, self.asset)

    def _sub(self):
        from toto.subscriptions.services import create_subscription
        return create_subscription(customer=self.customer, plan=self.plan, price=self.price, invoice_now=False)

    def test_renew_advances_period(self):
        from toto.subscriptions.services import renew_subscription
        sub = self._sub()
        old_end = sub.current_period_end
        renew_subscription(sub, invoice_now=False)
        sub.refresh_from_db()
        self.assertGreater(sub.current_period_end, old_end)
        self.assertEqual(sub.current_period_start, old_end)

    def test_renew_with_cancel_at_period_end_cancels(self):
        from toto.subscriptions.services import cancel_subscription, renew_subscription
        from toto.subscriptions.models import Subscription
        sub = self._sub()
        cancel_subscription(sub, at_period_end=True)
        renew_subscription(sub, invoice_now=False)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.CANCELLED)

    def test_renew_cancelled_raises(self):
        from toto.subscriptions.services import cancel_subscription, renew_subscription
        sub = self._sub()
        cancel_subscription(sub, at_period_end=False)
        with self.assertRaises(ValidationError):
            renew_subscription(sub, invoice_now=False)

    def test_renew_creates_invoice(self):
        from toto.subscriptions.services import renew_subscription
        sub = self._sub()
        renew_subscription(sub, invoice_now=True)
        self.assertEqual(sub.invoices.count(), 1)


# ---------------------------------------------------------------------------
# Service: create_invoice
# ---------------------------------------------------------------------------

class CreateInvoiceTests(TestCase):
    def setUp(self):
        self.community = _make_community("invoice-community")
        reserve = _make_ledger_account("inv-reserve")
        receiving = _make_ledger_account("inv-receiving")
        billing = _make_ledger_account("inv-billing")
        self.asset = _make_asset("INVT", reserve)
        self.plan = _make_plan(self.community, "inv-plan")
        self.price = _make_price(self.plan, self.asset, receiving, amount=5000)
        self.person = _make_person("inv-person")
        self.customer = _make_customer(self.community, self.person, billing, self.asset)

    def _sub(self):
        from toto.subscriptions.services import create_subscription
        return create_subscription(customer=self.customer, plan=self.plan, price=self.price, invoice_now=False)

    def test_creates_open_invoice(self):
        from toto.subscriptions.services import create_invoice
        from toto.subscriptions.models import SubscriptionInvoice
        sub = self._sub()
        invoice = create_invoice(sub)
        self.assertEqual(invoice.status, SubscriptionInvoice.Status.OPEN)
        self.assertEqual(invoice.total_base_units, 5000)

    def test_includes_subscription_line(self):
        from toto.subscriptions.services import create_invoice
        from toto.subscriptions.models import SubscriptionInvoiceLine
        sub = self._sub()
        invoice = create_invoice(sub)
        self.assertTrue(invoice.lines.filter(kind=SubscriptionInvoiceLine.LineKind.SUBSCRIPTION).exists())

    def test_setup_fee_included_when_requested(self):
        from toto.subscriptions.services import create_invoice
        from toto.subscriptions.models import SubscriptionInvoiceLine
        self.price.setup_fee_base_units = 1000
        self.price.save()
        sub = self._sub()
        invoice = create_invoice(sub, include_setup_fee=True)
        self.assertTrue(invoice.lines.filter(kind=SubscriptionInvoiceLine.LineKind.SETUP).exists())
        self.assertEqual(invoice.total_base_units, 6000)


# ---------------------------------------------------------------------------
# Service: pay_invoice (with mocked backend)
# ---------------------------------------------------------------------------

class PayInvoiceTests(TestCase):
    def setUp(self):
        self.community = _make_community("pay-community")
        reserve = _make_ledger_account("pay-reserve")
        self.receiving = _make_ledger_account("pay-receiving")
        self.billing = _make_ledger_account("pay-billing")
        self.asset = _make_asset("PAYT", reserve)
        self.plan = _make_plan(self.community, "pay-plan")
        self.price = _make_price(self.plan, self.asset, self.receiving, amount=2000)
        self.person = _make_person("pay-person")
        self.customer = _make_customer(self.community, self.person, self.billing, self.asset)

    def _sub_and_invoice(self):
        from toto.subscriptions.services import create_subscription, create_invoice
        sub = create_subscription(customer=self.customer, plan=self.plan, price=self.price, invoice_now=False)
        invoice = create_invoice(sub)
        return invoice

    def test_insufficient_balance_marks_invoice_failed(self):
        from toto.subscriptions.services import pay_invoice
        from toto.subscriptions.models import SubscriptionInvoice, Subscription
        invoice = self._sub_and_invoice()
        pay_invoice(invoice)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, SubscriptionInvoice.Status.FAILED)
        invoice.subscription.refresh_from_db()
        self.assertEqual(invoice.subscription.status, Subscription.Status.PAST_DUE)

    def _make_ledger_tx(self, ref="test-pay-tx"):
        from toto.assets.models import LedgerTransaction
        return LedgerTransaction.objects.create(reference=ref, transaction_type="transfer")

    @patch("toto.subscriptions.services.get_backend")
    def test_successful_payment_marks_invoice_paid(self, mock_get_backend):
        from toto.assets.models import AssetHolding
        from toto.subscriptions.services import pay_invoice
        from toto.subscriptions.models import SubscriptionInvoice, SubscriptionPayment

        AssetHolding.objects.create(
            account=self.billing,
            asset=self.asset,
            balance_base_units=99999,
        )
        mock_get_backend.return_value.transfer_asset.return_value = self._make_ledger_tx()

        invoice = self._sub_and_invoice()
        payment = pay_invoice(invoice)

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, SubscriptionInvoice.Status.PAID)
        self.assertEqual(payment.status, SubscriptionPayment.Status.SUCCEEDED)
        self.assertIsNotNone(payment.paid_at)

    @patch("toto.subscriptions.services.get_backend")
    def test_idempotent_on_already_paid(self, mock_get_backend):
        from toto.assets.models import AssetHolding
        from toto.subscriptions.services import pay_invoice

        AssetHolding.objects.create(account=self.billing, asset=self.asset, balance_base_units=99999)
        mock_get_backend.return_value.transfer_asset.return_value = self._make_ledger_tx("test-idempotent-tx")

        invoice = self._sub_and_invoice()
        p1 = pay_invoice(invoice)
        p2 = pay_invoice(invoice)
        self.assertEqual(p1.pk, p2.pk)


# ---------------------------------------------------------------------------
# Service: record_usage
# ---------------------------------------------------------------------------

class RecordUsageTests(TestCase):
    def setUp(self):
        self.community = _make_community("usage-community")
        reserve = _make_ledger_account("use-reserve")
        receiving = _make_ledger_account("use-receiving")
        billing = _make_ledger_account("use-billing")
        self.asset = _make_asset("USET", reserve)
        self.plan = _make_plan(self.community, "use-plan")
        self.price = _make_price(self.plan, self.asset, receiving)
        self.feature = _make_feature(self.plan, code="tokens", kind="allowance", quantity=Decimal("100"))
        self.person = _make_person("use-person")
        self.customer = _make_customer(self.community, self.person, billing, self.asset)
        from toto.subscriptions.services import create_subscription
        self.sub = create_subscription(customer=self.customer, plan=self.plan, price=self.price, invoice_now=False)

    def test_record_usage_applies_to_allowance(self):
        from toto.subscriptions.services import record_usage
        from toto.subscriptions.models import SubscriptionUsage
        usage = record_usage(subscription=self.sub, feature_code="tokens", quantity=Decimal("10"))
        self.assertEqual(usage.status, SubscriptionUsage.Status.APPLIED)
        allowance = self.sub.allowances.get(feature__code="tokens")
        self.assertEqual(allowance.consumed_quantity, Decimal("10"))

    def test_hard_limit_marks_over_limit(self):
        from toto.subscriptions.services import record_usage
        from toto.subscriptions.models import SubscriptionUsage
        self.feature.hard_limit = True
        self.feature.save()
        usage = record_usage(subscription=self.sub, feature_code="tokens", quantity=Decimal("200"))
        self.assertEqual(usage.status, SubscriptionUsage.Status.OVER_LIMIT)

    def test_zero_quantity_raises(self):
        from toto.subscriptions.services import record_usage
        with self.assertRaises(ValidationError):
            record_usage(subscription=self.sub, feature_code="tokens", quantity=Decimal("0"))

    def test_unknown_feature_raises(self):
        from toto.subscriptions.services import record_usage
        with self.assertRaises(Exception):
            record_usage(subscription=self.sub, feature_code="no-such-feature", quantity=Decimal("1"))


# ---------------------------------------------------------------------------
# Service: subscription_has_feature
# ---------------------------------------------------------------------------

class SubscriptionHasFeatureTests(TestCase):
    def setUp(self):
        self.community = _make_community("feat-check-community")
        reserve = _make_ledger_account("fc-reserve")
        receiving = _make_ledger_account("fc-receiving")
        billing = _make_ledger_account("fc-billing")
        self.asset = _make_asset("FCT", reserve)
        self.plan = _make_plan(self.community, "fc-plan")
        self.price = _make_price(self.plan, self.asset, receiving)
        _make_feature(self.plan, code="access", kind="entitlement", quantity=None)
        self.person = _make_person("fc-person")
        self.customer = _make_customer(self.community, self.person, billing, self.asset)
        from toto.subscriptions.services import create_subscription
        self.sub = create_subscription(customer=self.customer, plan=self.plan, price=self.price, invoice_now=False)

    def test_has_feature_true(self):
        from toto.subscriptions.services import subscription_has_feature
        self.assertTrue(subscription_has_feature(self.sub, "access"))

    def test_has_feature_false_for_unknown(self):
        from toto.subscriptions.services import subscription_has_feature
        self.assertFalse(subscription_has_feature(self.sub, "no-such-feature"))


# ---------------------------------------------------------------------------
# View auth checks
# ---------------------------------------------------------------------------

class SubscriptionViewAuthTests(TestCase):
    def setUp(self):
        from toto.core.models import Platform
        Platform.objects.create(site_name="Test", author="Test", publication_year=2024, active=True)

    def test_plan_list_accessible_anonymously(self):
        response = self.client.get("/subscriptions/")
        self.assertEqual(response.status_code, 200)

    def test_plan_create_requires_login(self):
        response = self.client.get("/subscriptions/plans/new/")
        self.assertEqual(response.status_code, 302)

    def test_customer_list_accessible_anonymously(self):
        response = self.client.get("/subscriptions/customers/")
        self.assertEqual(response.status_code, 200)

    def test_customer_create_requires_login(self):
        response = self.client.get("/subscriptions/customers/new/")
        self.assertEqual(response.status_code, 302)

    def test_subscription_list_accessible_anonymously(self):
        response = self.client.get("/subscriptions/subs/")
        self.assertEqual(response.status_code, 200)

    def test_subscription_create_requires_login(self):
        response = self.client.get("/subscriptions/subs/new/")
        self.assertEqual(response.status_code, 302)

    def test_invoice_list_accessible_anonymously(self):
        response = self.client.get("/subscriptions/invoices/")
        self.assertEqual(response.status_code, 200)

    def test_usage_list_accessible_anonymously(self):
        response = self.client.get("/subscriptions/usage/")
        self.assertEqual(response.status_code, 200)

    def test_metrics_accessible_anonymously(self):
        response = self.client.get("/subscriptions/metrics/")
        self.assertEqual(response.status_code, 200)
