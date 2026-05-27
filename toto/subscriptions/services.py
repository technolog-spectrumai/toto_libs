"""Domain services for toto.subscriptions.

The app calculates subscription state and delegates money movement to
`toto.assets.backend.get_backend()`. Do not edit holdings directly here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Iterable

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from toto.assets.backend import get_backend
from toto.assets.models import AssetHolding, from_base_units

from .models import (
    BillingInterval,
    Subscription,
    SubscriptionAllowance,
    SubscriptionCustomer,
    SubscriptionEntitlement,
    SubscriptionEvent,
    SubscriptionFeature,
    SubscriptionInvoice,
    SubscriptionInvoiceLine,
    SubscriptionPayment,
    SubscriptionPlan,
    SubscriptionPrice,
    SubscriptionUsage,
)


@dataclass(frozen=True)
class AffordabilityResult:
    ok: bool
    message: str = ""
    needed_base_units: int = 0
    balance_base_units: int = 0


# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------

def add_months(dt, months: int):
    """Small dependency-free month addition helper."""
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    max_day_by_month = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                        31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    day = min(dt.day, max_day_by_month[month - 1])
    return dt.replace(year=year, month=month, day=day)


def period_end_for(start, interval: str, interval_count: int = 1):
    interval_count = max(1, int(interval_count or 1))
    if interval == BillingInterval.DAILY:
        return start + timedelta(days=interval_count)
    if interval == BillingInterval.WEEKLY:
        return start + timedelta(weeks=interval_count)
    if interval == BillingInterval.MONTHLY:
        return add_months(start, interval_count)
    if interval == BillingInterval.QUARTERLY:
        return add_months(start, 3 * interval_count)
    if interval == BillingInterval.YEARLY:
        return add_months(start, 12 * interval_count)
    return start


def _event(community, kind, *, actor=None, subscription=None, target=None, metadata=None):
    kwargs = dict(community=community, kind=kind, actor=actor, subscription=subscription, metadata=metadata or {})
    if target is not None:
        kwargs["target"] = target
    return SubscriptionEvent.objects.create(**kwargs)


# ---------------------------------------------------------------------------
# Customers and subscriptions
# ---------------------------------------------------------------------------

def get_or_create_customer(*, community, person, billing_account, default_asset, label: str = "") -> SubscriptionCustomer:
    customer, created = SubscriptionCustomer.objects.update_or_create(
        community=community,
        person=person,
        defaults={
            "billing_account": billing_account,
            "default_asset": default_asset,
            "label": label,
            "status": SubscriptionCustomer.Status.ACTIVE,
        },
    )
    if created:
        _event(community, SubscriptionEvent.Kind.CUSTOMER_CREATED, target=customer)
    return customer


def grant_entitlements(subscription: Subscription) -> list[SubscriptionEntitlement]:
    """Create entitlement records for active plan features."""
    entitlements = []
    for feature in subscription.plan.features.filter(active=True):
        entitlement, _ = SubscriptionEntitlement.objects.update_or_create(
            subscription=subscription,
            feature=feature,
            defaults={
                "status": SubscriptionEntitlement.Status.ACTIVE,
                "starts_at": subscription.current_period_start,
                "ends_at": subscription.current_period_end,
            },
        )
        entitlements.append(entitlement)
    return entitlements


def reset_allowances(subscription: Subscription) -> list[SubscriptionAllowance]:
    """Create period allowance rows for allowance/metered features."""
    allowances = []
    features = subscription.plan.features.filter(
        active=True,
        kind__in=[SubscriptionFeature.FeatureKind.ALLOWANCE, SubscriptionFeature.FeatureKind.METERED],
    )
    for feature in features:
        included = feature.quantity or Decimal("0")
        allowance, _ = SubscriptionAllowance.objects.update_or_create(
            subscription=subscription,
            feature=feature,
            period_start=subscription.current_period_start,
            defaults={
                "period_end": subscription.current_period_end,
                "included_quantity": included,
                "consumed_quantity": Decimal("0"),
            },
        )
        allowances.append(allowance)
    return allowances


@transaction.atomic
def create_subscription(
    *,
    customer: SubscriptionCustomer,
    plan: SubscriptionPlan,
    price: SubscriptionPrice,
    starts_at=None,
    actor=None,
    source=None,
    invoice_now: bool = True,
    pay_now: bool = False,
) -> Subscription:
    if price.plan_id != plan.id:
        raise ValidationError("Price must belong to plan.")
    if customer.community_id != plan.community_id:
        raise ValidationError("Customer and plan must belong to the same community.")

    starts_at = starts_at or timezone.now()
    period_start = starts_at
    period_end = period_end_for(period_start, price.interval, price.interval_count)
    status = Subscription.Status.ACTIVE
    trial_ends_at = None
    if plan.trial_days:
        trial_ends_at = starts_at + timedelta(days=plan.trial_days)
        status = Subscription.Status.TRIALING

    create_kwargs = dict(
        customer=customer,
        plan=plan,
        price=price,
        status=status,
        starts_at=starts_at,
        current_period_start=period_start,
        current_period_end=period_end,
        trial_ends_at=trial_ends_at,
    )
    if source is not None:
        create_kwargs["source"] = source
    subscription = Subscription.objects.create(**create_kwargs)
    grant_entitlements(subscription)
    reset_allowances(subscription)
    _event(plan.community, SubscriptionEvent.Kind.SUBSCRIPTION_CREATED, actor=actor, subscription=subscription)

    if invoice_now and not trial_ends_at:
        invoice = create_invoice(subscription, include_setup_fee=True)
        if pay_now:
            pay_invoice(invoice)
    return subscription


@transaction.atomic
def cancel_subscription(subscription: Subscription, *, at_period_end: bool = True, actor=None, reason: str = "") -> Subscription:
    if at_period_end:
        subscription.cancel_at_period_end = True
        subscription.save(update_fields=["cancel_at_period_end", "updated_at"])
    else:
        now = timezone.now()
        subscription.status = Subscription.Status.CANCELLED
        subscription.cancelled_at = now
        subscription.ended_at = now
        subscription.entitlements.update(status=SubscriptionEntitlement.Status.REVOKED, ends_at=now)
        subscription.save(update_fields=["status", "cancelled_at", "ended_at", "updated_at"])
    _event(
        subscription.plan.community,
        SubscriptionEvent.Kind.SUBSCRIPTION_CANCELLED,
        actor=actor,
        subscription=subscription,
        metadata={"at_period_end": at_period_end, "reason": reason},
    )
    return subscription


@transaction.atomic
def renew_subscription(subscription: Subscription, *, invoice_now: bool = True, pay_now: bool = False) -> Subscription:
    if subscription.status in [Subscription.Status.CANCELLED, Subscription.Status.EXPIRED]:
        raise ValidationError("Cannot renew cancelled/expired subscription.")
    if subscription.cancel_at_period_end:
        now = timezone.now()
        subscription.status = Subscription.Status.CANCELLED
        subscription.ended_at = now
        subscription.cancelled_at = subscription.cancelled_at or now
        subscription.save(update_fields=["status", "ended_at", "cancelled_at", "updated_at"])
        return subscription

    subscription.current_period_start = subscription.current_period_end or timezone.now()
    subscription.current_period_end = period_end_for(
        subscription.current_period_start,
        subscription.price.interval,
        subscription.price.interval_count,
    )
    subscription.status = Subscription.Status.ACTIVE
    subscription.save(update_fields=["current_period_start", "current_period_end", "status", "updated_at"])
    grant_entitlements(subscription)
    reset_allowances(subscription)
    _event(subscription.plan.community, SubscriptionEvent.Kind.SUBSCRIPTION_RENEWED, subscription=subscription)

    if invoice_now:
        invoice = create_invoice(subscription)
        if pay_now:
            pay_invoice(invoice)
    return subscription


def renew_due_subscriptions(*, now=None, pay_now: bool = False, limit: int = 100) -> int:
    now = now or timezone.now()
    qs = Subscription.objects.filter(
        status__in=[Subscription.Status.ACTIVE, Subscription.Status.PAST_DUE],
        current_period_end__lte=now,
    ).order_by("current_period_end")[:limit]
    count = 0
    for subscription in qs:
        renew_subscription(subscription, invoice_now=True, pay_now=pay_now)
        count += 1
    return count


# ---------------------------------------------------------------------------
# Invoices and payments
# ---------------------------------------------------------------------------

def check_can_afford_invoice(invoice: SubscriptionInvoice) -> AffordabilityResult:
    holding = AssetHolding.objects.filter(
        account=invoice.customer.billing_account,
        asset=invoice.price.asset,
    ).first()
    balance = holding.balance_base_units if holding else 0
    needed = max(0, invoice.total_base_units - invoice.paid_base_units)
    if balance < needed:
        display_needed = from_base_units(needed, invoice.price.asset.decimals)
        display_balance = from_base_units(balance, invoice.price.asset.decimals)
        return AffordabilityResult(False, f"Insufficient {invoice.price.asset.unit_name}: need {display_needed}, have {display_balance}.", needed, balance)
    return AffordabilityResult(True, "", needed, balance)


@transaction.atomic
def create_invoice(subscription: Subscription, *, include_setup_fee: bool = False, due_at=None) -> SubscriptionInvoice:
    price = subscription.price
    invoice = SubscriptionInvoice.objects.create(
        subscription=subscription,
        customer=subscription.customer,
        price=price,
        status=SubscriptionInvoice.Status.DRAFT,
        period_start=subscription.current_period_start,
        period_end=subscription.current_period_end,
        due_at=due_at or timezone.now(),
    )
    if price.amount_base_units:
        SubscriptionInvoiceLine.objects.create(
            invoice=invoice,
            kind=SubscriptionInvoiceLine.LineKind.SUBSCRIPTION,
            description=f"{subscription.plan.name} / {price.get_interval_display()}",
            quantity=Decimal("1"),
            unit_amount_base_units=price.amount_base_units,
        )
    if include_setup_fee and price.setup_fee_base_units:
        SubscriptionInvoiceLine.objects.create(
            invoice=invoice,
            kind=SubscriptionInvoiceLine.LineKind.SETUP,
            description="Setup fee",
            quantity=Decimal("1"),
            unit_amount_base_units=price.setup_fee_base_units,
        )
    invoice.recalculate_totals()
    invoice.status = SubscriptionInvoice.Status.OPEN
    invoice.save(update_fields=["subtotal_base_units", "total_base_units", "status", "updated_at"])
    _event(subscription.plan.community, SubscriptionEvent.Kind.INVOICE_CREATED, subscription=subscription, target=invoice)
    return invoice


@transaction.atomic
def pay_invoice(invoice: SubscriptionInvoice, *, reference: str | None = None) -> SubscriptionPayment:
    if invoice.status == SubscriptionInvoice.Status.PAID:
        existing = invoice.payments.filter(status=SubscriptionPayment.Status.SUCCEEDED).order_by("-created_at").first()
        if existing:
            return existing
        raise ValidationError("Invoice is marked paid but has no successful payment.")

    invoice.recalculate_totals()
    invoice.save(update_fields=["subtotal_base_units", "total_base_units", "updated_at"])
    result = check_can_afford_invoice(invoice)
    ref = reference or f"subscription-invoice-{invoice.uid}"
    amount = max(0, invoice.total_base_units - invoice.paid_base_units)
    payment = SubscriptionPayment.objects.create(
        invoice=invoice,
        payer_account=invoice.customer.billing_account,
        receiving_account=invoice.price.receiving_account,
        amount_base_units=amount,
        reference=ref,
    )
    if not result.ok:
        payment.status = SubscriptionPayment.Status.FAILED
        payment.error_message = result.message
        payment.save(update_fields=["status", "error_message", "updated_at"])
        invoice.status = SubscriptionInvoice.Status.FAILED
        invoice.error_message = result.message
        invoice.subscription.status = Subscription.Status.PAST_DUE
        invoice.subscription.save(update_fields=["status", "updated_at"])
        invoice.save(update_fields=["status", "error_message", "updated_at"])
        _event(invoice.subscription.plan.community, SubscriptionEvent.Kind.PAYMENT_FAILED, subscription=invoice.subscription, target=payment, metadata={"error": result.message})
        return payment

    backend = get_backend()
    display_amount = from_base_units(amount, invoice.price.asset.decimals)
    tx = backend.transfer_asset(
        asset=invoice.price.asset,
        sender_account=invoice.customer.billing_account,
        receiver_account=invoice.price.receiving_account,
        amount=display_amount,
        reference=ref,
        description=f"Subscription invoice {invoice.uid}",
    )
    now = timezone.now()
    payment.status = SubscriptionPayment.Status.SUCCEEDED
    payment.ledger_transaction = tx
    payment.paid_at = now
    payment.save(update_fields=["status", "ledger_transaction", "paid_at", "updated_at"])
    invoice.status = SubscriptionInvoice.Status.PAID
    invoice.paid_base_units = invoice.total_base_units
    invoice.ledger_transaction = tx
    invoice.error_message = ""
    invoice.save(update_fields=["status", "paid_base_units", "ledger_transaction", "error_message", "updated_at"])
    invoice.subscription.status = Subscription.Status.ACTIVE
    invoice.subscription.save(update_fields=["status", "updated_at"])
    _event(invoice.subscription.plan.community, SubscriptionEvent.Kind.INVOICE_PAID, subscription=invoice.subscription, target=invoice)
    return payment


# ---------------------------------------------------------------------------
# Usage and allowance
# ---------------------------------------------------------------------------

def current_allowance(subscription: Subscription, feature: SubscriptionFeature) -> SubscriptionAllowance | None:
    return subscription.allowances.filter(
        feature=feature,
        period_start=subscription.current_period_start,
    ).first()


@transaction.atomic
def record_usage(
    *,
    subscription: Subscription,
    feature_code: str,
    quantity: Decimal,
    unit: str = "",
    source=None,
    note: str = "",
) -> SubscriptionUsage:
    feature = subscription.plan.features.get(code=feature_code, active=True)
    quantity = Decimal(str(quantity))
    if quantity <= 0:
        raise ValidationError("Usage quantity must be positive.")
    allowance = current_allowance(subscription, feature)
    status = SubscriptionUsage.Status.APPLIED
    if allowance:
        if feature.hard_limit and allowance.remaining_quantity < quantity:
            status = SubscriptionUsage.Status.OVER_LIMIT
        else:
            allowance.consumed_quantity += quantity
            allowance.save(update_fields=["consumed_quantity", "updated_at"])
    usage_kwargs = dict(
        subscription=subscription,
        feature=feature,
        allowance=allowance,
        quantity=quantity,
        unit=unit or feature.unit,
        status=status,
        note=note,
    )
    if source is not None:
        usage_kwargs["source"] = source
    usage = SubscriptionUsage.objects.create(**usage_kwargs)
    _event(subscription.plan.community, SubscriptionEvent.Kind.USAGE_RECORDED, subscription=subscription, target=usage)
    return usage


def subscription_has_feature(subscription: Subscription, feature_code: str) -> bool:
    return subscription.entitlements.filter(
        feature__code=feature_code,
        status=SubscriptionEntitlement.Status.ACTIVE,
    ).exists()


def active_subscriptions_for_person(person, community=None):
    qs = Subscription.objects.filter(
        customer__person=person,
        status__in=[Subscription.Status.ACTIVE, Subscription.Status.TRIALING],
    ).select_related("customer", "plan", "price")
    if community is not None:
        qs = qs.filter(customer__community=community)
    return qs
