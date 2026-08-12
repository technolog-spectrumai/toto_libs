from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q, Sum
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from toto.assets.models import Asset, AssetHolding, LedgerAccount
from toto.ui import PageProcessor

from .forms import TariffForm, TariffItemForm, UsageRecordForm, UsageSimulationForm
from .models import Tariff, TariffItem, TariffStatus, UsageRecord, UsageStatus
from .services import (
    calculate_tariff_charge,
    post_usage_record,
    rate_usage_record,
    record_and_post_usage,
    simulate_tariff,
)


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _can_manage(user, tariff=None) -> bool:
    """Who may change what things cost.

    Staff, or the tariff's own owner. Staff matters because the platform rate
    card is deliberately ownerless — it belongs to the platform, not a person —
    and requiring ownership there would leave it editable by superusers alone.
    """
    if not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    return tariff is not None and tariff.owner_id is not None and tariff.owner_id == user.pk


def _require_manage(user, tariff=None):
    if not _can_manage(user, tariff):
        raise PermissionDenied


# ---------------------------------------------------------------------------
# Tariff list
# ---------------------------------------------------------------------------

@login_required
def tariff_list(request):
    qs = Tariff.objects.prefetch_related("items")
    status_filter = request.GET.get("status", "").strip()
    q = request.GET.get("q", "").strip()
    if status_filter:
        qs = qs.filter(status=status_filter)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q))

    total = Tariff.objects.count()
    active = Tariff.objects.filter(status=TariffStatus.ACTIVE).count()
    posted = UsageRecord.objects.filter(status=UsageStatus.POSTED).count()
    pending = UsageRecord.objects.filter(status=UsageStatus.PENDING).count()
    failed = UsageRecord.objects.filter(status=UsageStatus.FAILED).count()

    return _render(request, "tariffs/tariff_list.html", {
        "tariffs": qs,
        "status_filter": status_filter,
        "q": q,
        "status_choices": TariffStatus.choices,
        "total": total,
        "active_count": active,
        "posted_count": posted,
        "pending_count": pending,
        "failed_count": failed,
    })


# ---------------------------------------------------------------------------
# Tariff create / edit
# ---------------------------------------------------------------------------

@login_required
def tariff_create(request):
    _require_manage(request.user)
    if request.method == "POST":
        form = TariffForm(request.POST)
        if form.is_valid():
            tariff = form.save()
            messages.success(request, _("Tariff created."))
            return redirect("tariffs:tariff_detail", uuid=tariff.uuid)
    else:
        form = TariffForm()
    return _render(request, "tariffs/tariff_form.html", {"form": form, "action": _("Create Tariff")})


@login_required
def tariff_edit(request, uuid):
    tariff = get_object_or_404(Tariff, uuid=uuid)
    if not _can_manage(request.user, tariff):
        return HttpResponseForbidden(_("Only the tariff owner can edit this tariff."))
    if request.method == "POST":
        form = TariffForm(request.POST, instance=tariff)
        if form.is_valid():
            form.save()
            messages.success(request, _("Tariff updated."))
            return redirect("tariffs:tariff_detail", uuid=tariff.uuid)
    else:
        form = TariffForm(instance=tariff)
    return _render(request, "tariffs/tariff_form.html", {
        "form": form,
        "tariff": tariff,
        "action": _("Edit Tariff"),
    })


# ---------------------------------------------------------------------------
# Tariff detail
# ---------------------------------------------------------------------------

@login_required
def tariff_detail(request, uuid):
    """The rate card, plus — for whoever manages it — what has been billed on it.

    The prices stay readable by every signed-in user on purpose: gas.md's
    protection against being charged without being asked is that prices are
    published, so putting the rate card behind a staff gate would remove the
    guarantee. What is *not* public is who spent what — `recent_usage` names
    other people's payer accounts and `usage_stats` counts their activity, so
    both are withheld from everyone but a manager.
    """
    tariff = get_object_or_404(
        Tariff.objects.prefetch_related("items__charged_asset", "items__receiving_account"),
        uuid=uuid,
    )
    is_owner = _can_manage(request.user, tariff)

    recent_usage = []
    usage_stats = None
    if is_owner:
        recent_usage = UsageRecord.objects.filter(tariff=tariff).select_related(
            "payer_account"
        ).order_by("-created_at")[:20]

        usage_stats = {
            "posted": UsageRecord.objects.filter(tariff=tariff, status=UsageStatus.POSTED).count(),
            "pending": UsageRecord.objects.filter(tariff=tariff, status=UsageStatus.PENDING).count(),
            "failed": UsageRecord.objects.filter(tariff=tariff, status=UsageStatus.FAILED).count(),
            "total": UsageRecord.objects.filter(tariff=tariff).count(),
        }

    sim_form = UsageSimulationForm() if is_owner else None

    return _render(request, "tariffs/tariff_detail.html", {
        "tariff": tariff,
        "items": tariff.items.select_related("charged_asset", "receiving_account"),
        "recent_usage": recent_usage,
        "usage_stats": usage_stats,
        "sim_form": sim_form,
        "is_owner": is_owner,
    })


# ---------------------------------------------------------------------------
# TariffItem create / edit
# ---------------------------------------------------------------------------

@login_required
def tariff_item_create(request, uuid):
    tariff = get_object_or_404(Tariff, uuid=uuid)
    if not _can_manage(request.user, tariff):
        return HttpResponseForbidden(_("Only the tariff owner can add items."))
    if request.method == "POST":
        form = TariffItemForm(request.POST, tariff=tariff)
        if form.is_valid():
            item = form.save()
            messages.success(request, _("Tariff item added."))
            return redirect("tariffs:tariff_detail", uuid=tariff.uuid)
    else:
        form = TariffItemForm(tariff=tariff)
    return _render(request, "tariffs/tariff_item_form.html", {
        "form": form,
        "tariff": tariff,
        "action": _("Add Tariff Item"),
    })


@login_required
def tariff_item_edit(request, pk):
    item = get_object_or_404(TariffItem.objects.select_related("tariff"), pk=pk)
    if not _can_manage(request.user, item.tariff):
        return HttpResponseForbidden(_("Only the tariff owner can edit items."))
    if request.method == "POST":
        form = TariffItemForm(request.POST, instance=item, tariff=item.tariff)
        if form.is_valid():
            form.save()
            messages.success(request, _("Tariff item updated."))
            return redirect("tariffs:tariff_detail", uuid=item.tariff.uuid)
    else:
        form = TariffItemForm(instance=item, tariff=item.tariff)
    return _render(request, "tariffs/tariff_item_form.html", {
        "form": form,
        "tariff": item.tariff,
        "item": item,
        "action": _("Edit Tariff Item"),
    })


# ---------------------------------------------------------------------------
# Simulate
# ---------------------------------------------------------------------------

@login_required
def tariff_simulate(request, uuid):
    """Price a hypothetical action. A pricing tool, so it gates like the others."""
    tariff = get_object_or_404(Tariff, uuid=uuid)
    _require_manage(request.user, tariff)
    result = None
    if request.method == "POST":
        form = UsageSimulationForm(request.POST)
        if form.is_valid():
            result = simulate_tariff(tariff, [{
                "metric_code": form.cleaned_data["metric_code"],
                "quantity": form.cleaned_data["quantity"],
                "unit": form.cleaned_data["unit"],
            }])
    else:
        form = UsageSimulationForm()
    return _render(request, "tariffs/simulate.html", {
        "tariff": tariff,
        "form": form,
        "result": result,
    })


# ---------------------------------------------------------------------------
# Usage list
# ---------------------------------------------------------------------------

@login_required
def usage_list(request):
    """Metered actions and what they cost. Yours, unless you manage the platform.

    This used to list every user's records to anyone signed in, which leaked
    both who is using the platform and what they are paying for it.
    """
    qs = UsageRecord.objects.select_related("tariff", "payer_account", "ledger_transaction")

    is_manager = _can_manage(request.user)
    if not is_manager:
        qs = qs.filter(payer_account__user=request.user)

    status_filter = request.GET.get("status", "").strip()
    tariff_filter = request.GET.get("tariff", "").strip()
    metric_filter = request.GET.get("metric", "").strip()
    q = request.GET.get("q", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()

    if status_filter:
        qs = qs.filter(status=status_filter)
    if tariff_filter:
        qs = qs.filter(tariff__code=tariff_filter)
    if metric_filter:
        qs = qs.filter(metric_code__icontains=metric_filter)
    if q:
        qs = qs.filter(
            Q(metric_code__icontains=q)
            | Q(source_type__icontains=q)
            | Q(source_id__icontains=q)
        )
    if date_from:
        qs = qs.filter(occurred_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(occurred_at__date__lte=date_to)

    return _render(request, "tariffs/usage_list.html", {
        "usage_records": qs[:200],
        "total_count": qs.count(),
        "status_choices": UsageStatus.choices,
        "status_filter": status_filter,
        "tariff_filter": tariff_filter,
        "metric_filter": metric_filter,
        "q": q,
        "date_from": date_from,
        "date_to": date_to,
        # The tariff picker is a platform-wide view of who charges what; a user
        # filtering their own history has no use for tariffs they never paid.
        "tariffs": (Tariff.objects.filter(status=TariffStatus.ACTIVE).order_by("code")
                    if is_manager else []),
        "is_manager": is_manager,
    })


# ---------------------------------------------------------------------------
# Usage create
# ---------------------------------------------------------------------------

@login_required
def usage_create(request):
    _require_manage(request.user)
    if request.method == "POST":
        form = UsageRecordForm(request.POST)
        if form.is_valid():
            record = form.save(commit=False)
            if not record.occurred_at:
                record.occurred_at = timezone.now()
            record.save()
            messages.success(request, _("Usage record created."))
            return redirect("tariffs:usage_detail", uuid=record.uuid)
    else:
        form = UsageRecordForm()
    return _render(request, "tariffs/usage_form.html", {"form": form, "action": _("Record Usage")})


# ---------------------------------------------------------------------------
# Usage detail
# ---------------------------------------------------------------------------

@login_required
def usage_detail(request, uuid):
    record = get_object_or_404(
        UsageRecord.objects.select_related(
            "tariff", "payer_account", "ledger_transaction"
        ),
        uuid=uuid,
    )
    # The uuid is unguessable, but that is obscurity rather than access control —
    # and usage_list handed these out to everyone until now.
    if not _can_manage(request.user) and record.payer_account.user_id != request.user.pk:
        raise PermissionDenied

    charges = record.charges.select_related(
        "tariff_item", "charged_asset", "payer_account", "receiving_account"
    )
    ledger_entries = []
    if record.ledger_transaction:
        ledger_entries = record.ledger_transaction.entries.select_related("account", "asset")

    return _render(request, "tariffs/usage_detail.html", {
        "record": record,
        "charges": charges,
        "ledger_entries": ledger_entries,
    })


# ---------------------------------------------------------------------------
# Usage post action
# ---------------------------------------------------------------------------

@login_required
@require_POST
def usage_post(request, uuid):
    _require_manage(request.user)
    record = get_object_or_404(UsageRecord, uuid=uuid)
    if record.status == UsageStatus.POSTED:
        messages.info(request, _("Usage already posted."))
        return redirect("tariffs:usage_detail", uuid=record.uuid)
    try:
        tx = post_usage_record(record)
        if tx is None:
            # Nothing was priced, so nothing moved and there is no transaction
            # to name. Posted, not failed — free is the absence of a price.
            messages.success(request, _("Usage posted. Nothing was charged — this metric is free."))
        else:
            messages.success(request, _("Usage posted. Transaction: %(ref)s") % {"ref": tx.reference})
    except ValueError as exc:
        messages.error(request, str(exc))
    except Exception as exc:
        messages.error(request, _("Posting failed: %(err)s") % {"err": str(exc)})
    return redirect("tariffs:usage_detail", uuid=record.uuid)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@login_required
def metrics(request):
    """Platform billing overview. Staff only — there is no per-user reading of it.

    Every panel here is about other people: revenue by receiving account, and a
    list of which accounts are nearly out of gas.
    """
    _require_manage(request.user)

    tariff_filter = request.GET.get("tariff", "").strip()
    status_filter = request.GET.get("status", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()

    qs = UsageRecord.objects.all()
    if tariff_filter:
        qs = qs.filter(tariff__code=tariff_filter)
    if status_filter:
        qs = qs.filter(status=status_filter)
    if date_from:
        qs = qs.filter(occurred_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(occurred_at__date__lte=date_to)

    status_counts = {}
    for s in UsageStatus.values:
        status_counts[s] = qs.filter(status=s).count()

    # Usage by metric
    by_metric = (
        qs.values("metric_code")
        .annotate(count=Count("id"))
        .order_by("-count")[:20]
    )

    # Charges by asset
    from .models import UsageCharge
    charge_qs = UsageCharge.objects.filter(usage_record__in=qs)
    by_asset = list(
        charge_qs.values("charged_asset__unit_name", "charged_asset__decimals")
        .annotate(total=Sum("amount_base_units"))
        .order_by("-total")
    )

    # Revenue by receiving account
    by_account = list(
        charge_qs.values(
            "receiving_account__code", "receiving_account__name",
            "charged_asset__unit_name", "charged_asset__decimals",
        )
        .annotate(total=Sum("amount_base_units"))
        .order_by("-total")[:20]
    )

    # Convert to display units. These are integer base units straight out of
    # the aggregate; printing them raw shows 1000000 where the number is
    # 0.001 — the decimals were already being selected and never used.
    from toto.assets.models import from_base_units
    for row in (*by_asset, *by_account):
        row["total_display"] = from_base_units(
            row["total"] or 0, row.get("charged_asset__decimals") or 0
        )

    # Low balance accounts — only accounts that appear as tariff payers
    tariff_payer_ids = UsageRecord.objects.values_list("payer_account_id", flat=True).distinct()
    low_balance_accounts = (
        AssetHolding.objects.select_related("account", "asset")
        .filter(balance_base_units__lt=1000, account_id__in=tariff_payer_ids)
        .order_by("balance_base_units")[:20]
    )

    return _render(request, "tariffs/metrics.html", {
        "status_counts": status_counts,
        "total_records": qs.count(),
        "by_metric": list(by_metric),
        "by_asset": list(by_asset),
        "by_account": list(by_account),
        "low_balance_accounts": low_balance_accounts,
        "tariffs": Tariff.objects.all().order_by("code"),
        "status_choices": UsageStatus.choices,
        "tariff_filter": tariff_filter,
        "status_filter": status_filter,
        "date_from": date_from,
        "date_to": date_to,
    })


# ---------------------------------------------------------------------------
# JSON API: rate
# ---------------------------------------------------------------------------

@login_required
def api_rate(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        body = json.loads(request.body)
        tariff = get_object_or_404(Tariff, code=body["tariff_code"])
        metric_code = body["metric_code"]
        quantity = Decimal(str(body["quantity"]))
        unit_slug = body.get("unit", "custom")
    except (KeyError, json.JSONDecodeError, InvalidOperation) as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    drafts = calculate_tariff_charge(tariff, metric_code, quantity, unit_slug)
    return JsonResponse({
        "tariff_code": tariff.code,
        "metric_code": metric_code,
        "quantity": str(quantity),
        "unit": unit_slug,
        "charges": [
            {
                "item_code": d.tariff_item.metric.code,
                "asset": d.tariff_item.charged_asset.unit_name,
                "amount_base_units": d.amount_base_units,
            }
            for d in drafts
        ],
    })


# ---------------------------------------------------------------------------
# JSON API: post
# ---------------------------------------------------------------------------

@login_required
def api_post(request):
    _require_manage(request.user)
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        body = json.loads(request.body)
        tariff = get_object_or_404(Tariff, code=body["tariff_code"])
        payer_account = get_object_or_404(LedgerAccount, code=body["payer_account_code"])
        metric_code = body["metric_code"]
        quantity = Decimal(str(body["quantity"]))
        unit_slug = body.get("unit", "custom")
        source_type = body.get("source_type", "")
        source_id = body.get("source_id", "")
        metadata = body.get("metadata", {})
    except (KeyError, json.JSONDecodeError, InvalidOperation) as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    try:
        record, tx = record_and_post_usage(
            tariff=tariff,
            payer_account=payer_account,
            metric_code=metric_code,
            quantity=quantity,
            unit=unit_slug,
            source_type=source_type,
            source_id=source_id,
            metadata=metadata,
        )
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    return JsonResponse({
        "usage_record_uuid": str(record.uuid),
        "status": record.status,
        "ledger_transaction_reference": tx.reference,
    }, status=201)


# ---------------------------------------------------------------------------
# JSON API: metrics
# ---------------------------------------------------------------------------

@login_required
def api_metrics(request):
    """The JSON half of the metrics page, and gated the same way."""
    _require_manage(request.user)
    from toto.assets.models import from_base_units

    from .models import UsageCharge
    data = {
        "usage_total": UsageRecord.objects.count(),
        "usage_posted": UsageRecord.objects.filter(status=UsageStatus.POSTED).count(),
        "usage_pending": UsageRecord.objects.filter(status=UsageStatus.PENDING).count(),
        "usage_failed": UsageRecord.objects.filter(status=UsageStatus.FAILED).count(),
        "active_tariffs": Tariff.objects.filter(status=TariffStatus.ACTIVE).count(),
        # Carry decimals through the aggregate and convert, the same way the
        # HTML view does. Without them "total" is raw base units and no caller
        # can render it — the exact bug the note at the top of `metrics` records,
        # which lived on here after the page was fixed.
        "charges_by_asset": [
            {**row,
             "total_display": str(from_base_units(
                 row["total"] or 0, row.get("charged_asset__decimals") or 0))}
            for row in UsageCharge.objects
            .values("charged_asset__unit_name", "charged_asset__decimals")
            .annotate(total=Sum("amount_base_units"))
        ],
    }
    return JsonResponse(data)

