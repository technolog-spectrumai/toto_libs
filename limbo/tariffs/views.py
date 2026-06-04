from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Q, Sum
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from toto.assets.models import Asset, AssetHolding, LedgerAccount
from toto.ui import PageProcessor

from .forms import ApplyUsageStatementForm, TariffForm, TariffItemForm, UsageRecordForm, UsageSimulationForm
from .models import Tariff, TariffApplication, TariffApplicationStatus, TariffItem, TariffStatus, UsageRecord, UsageStatus
from .services import (
    calculate_tariff_charge,
    post_usage_record,
    rate_usage_record,
    record_and_post_usage,
    simulate_tariff,
)


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _can_edit_tariff(user, tariff) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return tariff.owner_id is not None and tariff.owner_id == user.pk


# ---------------------------------------------------------------------------
# Tariff list
# ---------------------------------------------------------------------------

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

def tariff_create(request):
    if request.method == "POST":
        form = TariffForm(request.POST)
        if form.is_valid():
            tariff = form.save()
            messages.success(request, _("Tariff created."))
            return redirect("tariffs:tariff_detail", uuid=tariff.uuid)
    else:
        form = TariffForm()
    return _render(request, "tariffs/tariff_form.html", {"form": form, "action": _("Create Tariff")})


def tariff_edit(request, uuid):
    tariff = get_object_or_404(Tariff, uuid=uuid)
    if not _can_edit_tariff(request.user, tariff):
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

def tariff_detail(request, uuid):
    tariff = get_object_or_404(
        Tariff.objects.prefetch_related("items__charged_asset", "items__receiving_account"),
        uuid=uuid,
    )
    recent_usage = UsageRecord.objects.filter(tariff=tariff).select_related(
        "payer_account"
    ).order_by("-created_at")[:20]

    usage_stats = {
        "posted": UsageRecord.objects.filter(tariff=tariff, status=UsageStatus.POSTED).count(),
        "pending": UsageRecord.objects.filter(tariff=tariff, status=UsageStatus.PENDING).count(),
        "failed": UsageRecord.objects.filter(tariff=tariff, status=UsageStatus.FAILED).count(),
        "total": UsageRecord.objects.filter(tariff=tariff).count(),
    }

    sim_form = UsageSimulationForm()

    return _render(request, "tariffs/tariff_detail.html", {
        "tariff": tariff,
        "items": tariff.items.select_related("charged_asset", "receiving_account"),
        "recent_usage": recent_usage,
        "usage_stats": usage_stats,
        "sim_form": sim_form,
        "is_owner": _can_edit_tariff(request.user, tariff),
    })


# ---------------------------------------------------------------------------
# TariffItem create / edit
# ---------------------------------------------------------------------------

def tariff_item_create(request, uuid):
    tariff = get_object_or_404(Tariff, uuid=uuid)
    if not _can_edit_tariff(request.user, tariff):
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


def tariff_item_edit(request, pk):
    item = get_object_or_404(TariffItem.objects.select_related("tariff"), pk=pk)
    if not _can_edit_tariff(request.user, item.tariff):
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

def tariff_simulate(request, uuid):
    tariff = get_object_or_404(Tariff, uuid=uuid)
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

def usage_list(request):
    qs = UsageRecord.objects.select_related("tariff", "payer_account", "ledger_transaction")

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
        "tariffs": Tariff.objects.filter(status=TariffStatus.ACTIVE).order_by("code"),
    })


# ---------------------------------------------------------------------------
# Usage create
# ---------------------------------------------------------------------------

def usage_create(request):
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

def usage_detail(request, uuid):
    record = get_object_or_404(
        UsageRecord.objects.select_related(
            "tariff", "payer_account", "ledger_transaction"
        ),
        uuid=uuid,
    )
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

@require_POST
def usage_post(request, uuid):
    record = get_object_or_404(UsageRecord, uuid=uuid)
    if record.status == UsageStatus.POSTED:
        messages.info(request, _("Usage already posted."))
        return redirect("tariffs:usage_detail", uuid=record.uuid)
    try:
        tx = post_usage_record(record)
        messages.success(request, _("Usage posted. Transaction: %(ref)s") % {"ref": tx.reference})
    except ValueError as exc:
        messages.error(request, str(exc))
    except Exception as exc:
        messages.error(request, _("Posting failed: %(err)s") % {"err": str(exc)})
    return redirect("tariffs:usage_detail", uuid=record.uuid)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def metrics(request):
    from django.db.models.functions import TruncDate

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
    by_asset = (
        charge_qs.values("charged_asset__unit_name", "charged_asset__decimals")
        .annotate(total=Sum("amount_base_units"))
        .order_by("-total")
    )

    # Revenue by receiving account
    by_account = (
        charge_qs.values("receiving_account__code", "receiving_account__name", "charged_asset__unit_name")
        .annotate(total=Sum("amount_base_units"))
        .order_by("-total")[:20]
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

def api_post(request):
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

def api_metrics(request):
    from .models import UsageCharge
    data = {
        "usage_total": UsageRecord.objects.count(),
        "usage_posted": UsageRecord.objects.filter(status=UsageStatus.POSTED).count(),
        "usage_pending": UsageRecord.objects.filter(status=UsageStatus.PENDING).count(),
        "usage_failed": UsageRecord.objects.filter(status=UsageStatus.FAILED).count(),
        "active_tariffs": Tariff.objects.filter(status=TariffStatus.ACTIVE).count(),
        "charges_by_asset": list(
            UsageCharge.objects.values("charged_asset__unit_name")
            .annotate(total=Sum("amount_base_units"))
        ),
    }
    return JsonResponse(data)


# ---------------------------------------------------------------------------
# Apply Usage Statement
# ---------------------------------------------------------------------------

def apply_usage_statement(request):
    from toto.tariffs.usage_statement_services import (
        preview_usage_statement,
        price_usage_statement,
        apply_tariff_to_usage_statement,
    )

    form = ApplyUsageStatementForm(request.POST or None)
    preview = None
    pricing = None
    action = request.POST.get("action", "")

    # Auto-preview when usage_file query param is present (GET)
    if request.method == "GET":
        file_id = request.GET.get("usage_file")
        if file_id:
            try:
                from toto.vault.models import VaultFile
                vf = VaultFile.objects.get(pk=file_id)
                preview = preview_usage_statement(usage_file=vf)
                form = ApplyUsageStatementForm(initial={"usage_file": vf})
            except Exception as exc:
                messages.error(request, _("Preview failed: %(e)s") % {"e": exc})

    elif request.method == "POST":
        if form.is_valid():
            usage_file = form.cleaned_data["usage_file"]
            tariff = form.cleaned_data["tariff"]

            # Always show preview first
            try:
                preview = preview_usage_statement(usage_file=usage_file)
            except Exception as exc:
                messages.error(request, _("Preview failed: %(e)s") % {"e": exc})

            if action == "preview":
                try:
                    pricing = price_usage_statement(usage_file=usage_file, tariff=tariff)
                except Exception as exc:
                    messages.error(request, _("Pricing failed: %(e)s") % {"e": exc})

            elif action == "apply":
                try:
                    pricing = price_usage_statement(usage_file=usage_file, tariff=tariff)
                    if pricing and pricing.get("has_errors"):
                        for err in pricing["errors"]:
                            messages.error(request, err.get("error", str(err)))
                    else:
                        app, invoice = apply_tariff_to_usage_statement(
                            usage_file=usage_file,
                            tariff=tariff,
                            issued_to=form.cleaned_data["issued_to"],
                            issued_by=request.user if request.user.is_authenticated else None,
                            due_date=form.cleaned_data.get("due_date"),
                            title=form.cleaned_data.get("title") or None,
                            description=form.cleaned_data.get("description", ""),
                            override_source_app=form.cleaned_data.get("override_source_app", ""),
                            override_subject_label=form.cleaned_data.get("override_subject_label", ""),
                        )
                        messages.success(
                            request,
                            _("Invoice #%(pk)s created for usage statement %(sid)s.")
                            % {"pk": invoice.pk, "sid": app.statement_id},
                        )
                        return redirect("tariffs:tariff_application_detail", uid=app.uid)
                except ValidationError as exc:
                    for field, errs in exc.message_dict.items():
                        for e in errs:
                            messages.error(request, f"{field}: {e}")
                except Exception as exc:
                    messages.error(request, _("Apply failed: %(e)s") % {"e": exc})

    return _render(request, "tariffs/apply_usage_statement.html", {
        "form": form,
        "preview": preview,
        "pricing": pricing,
        "action": action,
    })


# ---------------------------------------------------------------------------
# Tariff Application List
# ---------------------------------------------------------------------------

def tariff_application_list(request):
    qs = TariffApplication.objects.select_related("tariff", "usage_file", "created_by")

    source_app_filter = request.GET.get("source_app", "").strip()
    status_filter = request.GET.get("status", "").strip()
    tariff_filter = request.GET.get("tariff", "").strip()
    subject_key_filter = request.GET.get("subject_key", "").strip()

    if source_app_filter:
        qs = qs.filter(detected_source_app=source_app_filter)
    if status_filter:
        qs = qs.filter(status=status_filter)
    if tariff_filter:
        qs = qs.filter(tariff__code=tariff_filter)
    if subject_key_filter:
        qs = qs.filter(
            Q(detected_subject_key__icontains=subject_key_filter)
        )

    return _render(request, "tariffs/tariff_application_list.html", {
        "applications": qs[:200],
        "total_count": qs.count(),
        "status_choices": TariffApplicationStatus.choices,
        "tariffs": Tariff.objects.filter(status=TariffStatus.ACTIVE).order_by("code"),
        "source_app_filter": source_app_filter,
        "status_filter": status_filter,
        "tariff_filter": tariff_filter,
        "subject_key_filter": subject_key_filter,
        "distinct_source_apps": TariffApplication.objects.exclude(detected_source_app="")
            .values_list("detected_source_app", flat=True).distinct().order_by("detected_source_app"),
    })


# ---------------------------------------------------------------------------
# Tariff Application Detail
# ---------------------------------------------------------------------------

def tariff_application_detail(request, uid):
    app = get_object_or_404(
        TariffApplication.objects.select_related("tariff", "usage_file", "created_by"),
        uid=uid,
    )
    lines = app.lines.select_related("tariff_item__metric", "charged_asset")

    invoice = None
    if app.invoice_source_type == "invoice.Invoice" and app.invoice_source_id:
        try:
            from toto.invoice.models import Invoice
            invoice = Invoice.objects.get(pk=int(app.invoice_source_id))
        except Exception:
            pass

    # Totals by asset
    totals_by_asset: dict = {}
    for line in lines:
        key = line.charged_asset.unit_name
        if key not in totals_by_asset:
            totals_by_asset[key] = {"asset": key, "base_units": 0, "display": 0}
        totals_by_asset[key]["base_units"] += line.amount_base_units
        totals_by_asset[key]["display"] = totals_by_asset[key]["display"] + line.amount_display

    return _render(request, "tariffs/tariff_application_detail.html", {
        "app": app,
        "lines": lines,
        "invoice": invoice,
        "totals_by_asset": list(totals_by_asset.values()),
    })


# ---------------------------------------------------------------------------
# Optional JSON endpoints
# ---------------------------------------------------------------------------

def preview_usage_statement_json(request):
    file_id = request.GET.get("usage_file")
    if not file_id:
        return JsonResponse({"error": "usage_file param required"}, status=400)
    try:
        from toto.vault.models import VaultFile
        from toto.tariffs.usage_statement_services import preview_usage_statement
        vf = VaultFile.objects.get(pk=file_id)
        preview = preview_usage_statement(usage_file=vf)
        from decimal import Decimal
        import json as _json
        return JsonResponse(_decimal_safe(preview), safe=False)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=400)


def price_usage_statement_json(request):
    file_id = request.GET.get("usage_file")
    tariff_code = request.GET.get("tariff")
    if not file_id or not tariff_code:
        return JsonResponse({"error": "usage_file and tariff params required"}, status=400)
    try:
        from toto.vault.models import VaultFile
        from toto.tariffs.usage_statement_services import price_usage_statement
        vf = VaultFile.objects.get(pk=file_id)
        tariff = Tariff.objects.get(code=tariff_code)
        pricing = price_usage_statement(usage_file=vf, tariff=tariff)
        return JsonResponse(_decimal_safe(pricing), safe=False)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=400)


def metering_overview(request):
    """
    Aggregate view of raw metering events across all installed apps.

    Queries every AbstractUsageEvent subclass registered in the app registry
    to show per-app event counts and the 50 most recent events globally.
    """
    from django.apps import apps as django_apps
    from toto.metering.models import AbstractUsageEvent

    rows = []
    recent_events = []

    for model in django_apps.get_models():
        if model is AbstractUsageEvent:
            continue
        if not issubclass(model, AbstractUsageEvent):
            continue
        app_label = model._meta.app_label
        count = model.objects.count()
        latest = model.objects.order_by("-occurred_at").values("metric_code", "quantity", "unit", "occurred_at", "source_type").first()
        rows.append({
            "app_label": app_label,
            "model": model.__name__,
            "count": count,
            "latest": latest,
        })
        recent_events += list(
            model.objects.order_by("-occurred_at").values(
                "metric_code", "quantity", "unit", "occurred_at",
                "source_type", "source_id", "description", "status",
            )[:20]
        )

    rows.sort(key=lambda r: r["app_label"])
    recent_events.sort(key=lambda e: e["occurred_at"] or "", reverse=True)

    return _render(request, "tariffs/metering_overview.html", {
        "rows": rows,
        "recent_events": recent_events[:50],
        "total_apps": len(rows),
        "total_events": sum(r["count"] for r in rows),
    })


def _decimal_safe(obj):
    """Recursively convert Decimal to str for JSON serialisation."""
    from decimal import Decimal
    if isinstance(obj, dict):
        return {k: _decimal_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimal_safe(i) for i in obj]
    if isinstance(obj, Decimal):
        return str(obj)
    return obj
