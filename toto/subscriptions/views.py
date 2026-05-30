from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor

from .forms import (
    SubscriptionCreateForm,
    SubscriptionCustomerForm,
    SubscriptionFeatureForm,
    SubscriptionPlanForm,
    SubscriptionPriceForm,
    UsageRecordForm,
)
from .models import (
    Subscription,
    SubscriptionCustomer,
    SubscriptionFeature,
    SubscriptionInvoice,
    SubscriptionPayment,
    SubscriptionPlan,
    SubscriptionPrice,
    SubscriptionUsage,
)
from .services import cancel_subscription, create_invoice, create_subscription, pay_invoice, renew_due_subscriptions, renew_subscription


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _can_manage(user, obj=None) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    # Domain-level permissions can be added here later.
    return False


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------

def plan_list(request):
    qs = SubscriptionPlan.objects.select_related("community", "owner").prefetch_related("prices", "features")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    kind = request.GET.get("kind", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(description__icontains=q))
    if status:
        qs = qs.filter(status=status)
    if kind:
        qs = qs.filter(kind=kind)
    stats = {
        "plans": SubscriptionPlan.objects.count(),
        "active": SubscriptionPlan.objects.filter(status=SubscriptionPlan.Status.ACTIVE).count(),
        "subscriptions": Subscription.objects.count(),
        "past_due": Subscription.objects.filter(status=Subscription.Status.PAST_DUE).count(),
    }
    return _render(request, "subscriptions/plan_list.html", {
        "plans": qs[:200],
        "q": q,
        "status_filter": status,
        "kind_filter": kind,
        "status_choices": SubscriptionPlan.Status.choices,
        "kind_choices": SubscriptionPlan.PlanKind.choices,
        "stats": stats,
    })


@login_required
def plan_create(request):
    if not _can_manage(request.user):
        return HttpResponseForbidden(_("Only staff can create subscription plans."))
    if request.method == "POST":
        form = SubscriptionPlanForm(request.POST)
        if form.is_valid():
            plan = form.save()
            messages.success(request, _("Subscription plan created."))
            return redirect("subscriptions:plan_detail", uid=plan.uid)
    else:
        form = SubscriptionPlanForm()
    return _render(request, "subscriptions/plan_form.html", {"form": form, "action": _("Create Plan")})


def plan_detail(request, uid):
    plan = get_object_or_404(SubscriptionPlan.objects.select_related("community", "owner"), uid=uid)
    prices = plan.prices.select_related("asset", "receiving_account")
    features = plan.features.all()
    active_subscriptions = plan.subscriptions.filter(status__in=[Subscription.Status.ACTIVE, Subscription.Status.TRIALING]).count()
    return _render(request, "subscriptions/plan_detail.html", {
        "plan": plan,
        "prices": prices,
        "features": features,
        "active_subscriptions": active_subscriptions,
        "can_manage": _can_manage(request.user, plan),
    })


@login_required
def plan_edit(request, uid):
    plan = get_object_or_404(SubscriptionPlan, uid=uid)
    if not _can_manage(request.user, plan):
        return HttpResponseForbidden(_("Only staff can edit subscription plans."))
    if request.method == "POST":
        form = SubscriptionPlanForm(request.POST, instance=plan)
        if form.is_valid():
            form.save()
            messages.success(request, _("Subscription plan updated."))
            return redirect("subscriptions:plan_detail", uid=plan.uid)
    else:
        form = SubscriptionPlanForm(instance=plan)
    return _render(request, "subscriptions/plan_form.html", {"form": form, "plan": plan, "action": _("Edit Plan")})


@login_required
def price_create(request, plan_uid):
    plan = get_object_or_404(SubscriptionPlan, uid=plan_uid)
    if not _can_manage(request.user, plan):
        return HttpResponseForbidden(_("Only staff can add prices."))
    if request.method == "POST":
        form = SubscriptionPriceForm(request.POST, plan=plan)
        if form.is_valid():
            form.save()
            messages.success(request, _("Price added."))
            return redirect("subscriptions:plan_detail", uid=plan.uid)
    else:
        form = SubscriptionPriceForm(plan=plan)
    return _render(request, "subscriptions/price_form.html", {"form": form, "plan": plan, "action": _("Add Price")})


@login_required
def feature_create(request, plan_uid):
    plan = get_object_or_404(SubscriptionPlan, uid=plan_uid)
    if not _can_manage(request.user, plan):
        return HttpResponseForbidden(_("Only staff can add features."))
    if request.method == "POST":
        form = SubscriptionFeatureForm(request.POST, plan=plan)
        if form.is_valid():
            form.save()
            messages.success(request, _("Feature added."))
            return redirect("subscriptions:plan_detail", uid=plan.uid)
    else:
        form = SubscriptionFeatureForm(plan=plan)
    return _render(request, "subscriptions/feature_form.html", {"form": form, "plan": plan, "action": _("Add Feature")})


# ---------------------------------------------------------------------------
# Customers and subscriptions
# ---------------------------------------------------------------------------

def customer_list(request):
    qs = SubscriptionCustomer.objects.select_related("community", "person", "billing_account", "default_asset")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if q:
        qs = qs.filter(Q(label__icontains=q) | Q(person__name__icontains=q) | Q(billing_account__code__icontains=q))
    if status:
        qs = qs.filter(status=status)
    return _render(request, "subscriptions/customer_list.html", {
        "customers": qs[:200],
        "q": q,
        "status_filter": status,
        "status_choices": SubscriptionCustomer.Status.choices,
    })


@login_required
def customer_create(request):
    if not _can_manage(request.user):
        return HttpResponseForbidden(_("Only staff can create customers."))
    if request.method == "POST":
        form = SubscriptionCustomerForm(request.POST)
        if form.is_valid():
            customer = form.save()
            messages.success(request, _("Customer created."))
            return redirect("subscriptions:customer_detail", uid=customer.uid)
    else:
        form = SubscriptionCustomerForm()
    return _render(request, "subscriptions/customer_form.html", {"form": form, "action": _("Create Customer")})


def customer_detail(request, uid):
    customer = get_object_or_404(SubscriptionCustomer.objects.select_related("community", "person", "billing_account", "default_asset"), uid=uid)
    subscriptions = customer.subscriptions.select_related("plan", "price").order_by("-created_at")
    invoices = customer.subscription_invoices.select_related("subscription", "price").order_by("-created_at")[:20]
    return _render(request, "subscriptions/customer_detail.html", {
        "customer": customer,
        "subscriptions": subscriptions,
        "invoices": invoices,
        "can_manage": _can_manage(request.user, customer),
    })


def subscription_list(request):
    qs = Subscription.objects.select_related("customer", "customer__person", "plan", "price")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if q:
        qs = qs.filter(Q(plan__name__icontains=q) | Q(plan__code__icontains=q) | Q(customer__person__name__icontains=q))
    if status:
        qs = qs.filter(status=status)
    counts = {row["status"]: row["count"] for row in Subscription.objects.values("status").annotate(count=Count("id"))}
    return _render(request, "subscriptions/subscription_list.html", {
        "subscriptions": qs[:200],
        "q": q,
        "status_filter": status,
        "status_choices": Subscription.Status.choices,
        "counts": counts,
    })


@login_required
def subscription_create(request):
    if not _can_manage(request.user):
        return HttpResponseForbidden(_("Only staff can create subscriptions."))
    if request.method == "POST":
        form = SubscriptionCreateForm(request.POST)
        if form.is_valid():
            subscription = create_subscription(
                customer=form.cleaned_data["customer"],
                plan=form.cleaned_data["plan"],
                price=form.cleaned_data["price"],
                starts_at=form.cleaned_data.get("starts_at"),
                pay_now=form.cleaned_data.get("pay_now", False),
            )
            messages.success(request, _("Subscription created."))
            return redirect("subscriptions:subscription_detail", uid=subscription.uid)
    else:
        form = SubscriptionCreateForm()
    return _render(request, "subscriptions/subscription_form.html", {"form": form, "action": _("Create Subscription")})


def subscription_detail(request, uid):
    subscription = get_object_or_404(Subscription.objects.select_related("customer", "customer__person", "plan", "price"), uid=uid)
    return _render(request, "subscriptions/subscription_detail.html", {
        "subscription": subscription,
        "entitlements": subscription.entitlements.select_related("feature"),
        "allowances": subscription.allowances.select_related("feature").order_by("-period_start"),
        "usage_records": subscription.usage_records.select_related("feature").order_by("-occurred_at")[:30],
        "invoices": subscription.invoices.select_related("price").order_by("-created_at")[:30],
        "can_manage": _can_manage(request.user, subscription),
    })


@login_required
@require_POST
def subscription_cancel(request, uid):
    subscription = get_object_or_404(Subscription, uid=uid)
    if not _can_manage(request.user, subscription):
        return HttpResponseForbidden(_("Only staff can cancel subscriptions."))
    cancel_subscription(subscription, at_period_end=request.POST.get("at_period_end", "1") != "0")
    messages.success(request, _("Subscription cancellation updated."))
    return redirect("subscriptions:subscription_detail", uid=subscription.uid)


@login_required
@require_POST
def subscription_renew(request, uid):
    subscription = get_object_or_404(Subscription, uid=uid)
    if not _can_manage(request.user, subscription):
        return HttpResponseForbidden(_("Only staff can renew subscriptions."))
    renew_subscription(subscription, invoice_now=True, pay_now=False)
    messages.success(request, _("Subscription renewed and invoice created."))
    return redirect("subscriptions:subscription_detail", uid=subscription.uid)


# ---------------------------------------------------------------------------
# Usage and invoices
# ---------------------------------------------------------------------------

def usage_list(request):
    qs = SubscriptionUsage.objects.select_related("subscription", "subscription__plan", "feature")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if q:
        qs = qs.filter(Q(feature__code__icontains=q) | Q(subscription__plan__code__icontains=q) | Q(note__icontains=q))
    if status:
        qs = qs.filter(status=status)
    return _render(request, "subscriptions/usage_list.html", {"usage_records": qs[:200], "q": q, "status_filter": status, "status_choices": SubscriptionUsage.Status.choices})


@login_required
def usage_create(request):
    if not _can_manage(request.user):
        return HttpResponseForbidden(_("Only staff can record usage."))
    if request.method == "POST":
        form = UsageRecordForm(request.POST)
        if form.is_valid():
            usage = form.save()
            messages.success(request, _("Usage recorded."))
            return redirect("subscriptions:subscription_detail", uid=usage.subscription.uid)
    else:
        form = UsageRecordForm()
    return _render(request, "subscriptions/usage_form.html", {"form": form, "action": _("Record Usage")})


def invoice_list(request):
    qs = SubscriptionInvoice.objects.select_related("subscription", "subscription__plan", "customer", "price")
    status = request.GET.get("status", "").strip()
    if status:
        qs = qs.filter(status=status)
    totals = qs.aggregate(total=Sum("total_base_units"), paid=Sum("paid_base_units"))
    return _render(request, "subscriptions/invoice_list.html", {"invoices": qs[:200], "status_filter": status, "status_choices": SubscriptionInvoice.Status.choices, "totals": totals})


def invoice_detail(request, uid):
    invoice = get_object_or_404(SubscriptionInvoice.objects.select_related("subscription", "subscription__plan", "customer", "price", "ledger_transaction"), uid=uid)
    return _render(request, "subscriptions/invoice_detail.html", {"invoice": invoice, "lines": invoice.lines.all(), "payments": invoice.payments.all(), "can_manage": _can_manage(request.user, invoice)})


@login_required
@require_POST
def invoice_create(request, subscription_uid):
    subscription = get_object_or_404(Subscription, uid=subscription_uid)
    if not _can_manage(request.user, subscription):
        return HttpResponseForbidden(_("Only staff can create invoices."))
    invoice = create_invoice(subscription)
    messages.success(request, _("Invoice created."))
    return redirect("subscriptions:invoice_detail", uid=invoice.uid)


@login_required
@require_POST
def invoice_pay(request, uid):
    invoice = get_object_or_404(SubscriptionInvoice, uid=uid)
    if not _can_manage(request.user, invoice):
        return HttpResponseForbidden(_("Only staff can pay invoices."))
    payment = pay_invoice(invoice)
    if payment.status == SubscriptionPayment.Status.SUCCEEDED:
        messages.success(request, _("Invoice paid."))
    else:
        messages.error(request, payment.error_message or _("Payment failed."))
    return redirect("subscriptions:invoice_detail", uid=invoice.uid)


# ---------------------------------------------------------------------------
# Metrics / APIs
# ---------------------------------------------------------------------------

def metrics(request):
    from django.db.models.functions import TruncMonth
    from django.utils import timezone as tz
    import json as _json

    status_counts = {
        row["status"]: row["count"]
        for row in Subscription.objects.values("status").annotate(count=Count("id"))
    }
    invoice_counts = {
        row["status"]: row["count"]
        for row in SubscriptionInvoice.objects.values("status").annotate(count=Count("id"))
    }
    by_plan = (
        Subscription.objects
        .values("plan__code", "plan__name")
        .annotate(count=Count("id"))
        .order_by("-count")[:20]
    )

    # MRR — sum of active subscription amounts normalised to monthly
    INTERVAL_MONTHS = {
        "once": None, "daily": 1/30, "weekly": 1/4.33,
        "monthly": 1, "quarterly": 1/3, "yearly": 1/12, "custom": None,
    }
    mrr_by_plan: dict = {}
    for sub in Subscription.objects.filter(status="active").select_related("price"):
        price = sub.price
        if not price:
            continue
        factor = INTERVAL_MONTHS.get(price.interval, None)
        if factor is None:
            continue
        plan_name = sub.plan.code if sub.plan else "?"
        mrr_by_plan[plan_name] = mrr_by_plan.get(plan_name, 0) + int(price.amount_base_units * factor)
    total_mrr = sum(mrr_by_plan.values())

    # Monthly payment totals (last 13 months)
    cutoff = tz.now() - tz.timedelta(days=395)
    monthly_payments = list(
        SubscriptionPayment.objects
        .filter(status="succeeded", created_at__gte=cutoff)
        .annotate(month=TruncMonth("created_at"))
        .values("month")
        .annotate(total=Sum("amount_base_units"))
        .order_by("month")
    )
    chart_labels = [r["month"].strftime("%Y-%m") for r in monthly_payments if r["month"]]
    chart_data   = [r["total"] for r in monthly_payments]

    return _render(request, "subscriptions/metrics.html", {
        "status_counts": status_counts,
        "invoice_counts": invoice_counts,
        "by_plan": by_plan,
        "mrr_by_plan": mrr_by_plan,
        "total_mrr": total_mrr,
        "chart_labels": _json.dumps(chart_labels),
        "chart_data": _json.dumps(chart_data),
    })


@login_required
@require_POST
def renew_due(request):
    if not _can_manage(request.user):
        return HttpResponseForbidden(_("Only staff can renew due subscriptions."))
    count = renew_due_subscriptions(pay_now=request.POST.get("pay_now") == "1")
    messages.success(request, _("Renewed %(count)s subscription(s).") % {"count": count})
    return redirect("subscriptions:subscription_list")


@require_POST
def api_record_usage(request):
    form = UsageRecordForm(request.POST)
    if form.is_valid():
        usage = form.save()
        return JsonResponse({"ok": True, "usage_uid": str(usage.uid), "status": usage.status})
    return JsonResponse({"ok": False, "errors": form.errors}, status=400)
