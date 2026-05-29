"""
Budget views — function-based, PageProcessor style.
"""
from __future__ import annotations

import json
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from toto.ui import PageProcessor

from .forms import BudgetForm, BudgetItemForm, BudgetLedgerAccountForm, BudgetStreamTypeForm
from .models import Budget, BudgetItem, BudgetLedgerAccount, BudgetStreamType
from .services import budget_flow_data, budget_metrics, budget_totals, import_budget_candidates


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _dec(obj):
    if isinstance(obj, dict):
        return {k: _dec(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_dec(i) for i in obj]
    if isinstance(obj, Decimal):
        return str(obj)
    return obj


# ---------------------------------------------------------------------------
# Budget list
# ---------------------------------------------------------------------------

@login_required
def budget_list(request):
    from .models import BudgetStatus
    qs = Budget.objects.select_related("asset", "budget_account")
    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    if q:
        qs = qs.filter(name__icontains=q)
    if status_filter:
        qs = qs.filter(status=status_filter)
    return _render(request, "budget/budget_list.html", {
        "budgets": qs,
        "q": q,
        "status_filter": status_filter,
        "status_choices": BudgetStatus.choices,
        "total": Budget.objects.count(),
        "active_count": Budget.objects.filter(status=BudgetStatus.ACTIVE).count(),
    })


# ---------------------------------------------------------------------------
# Budget detail
# ---------------------------------------------------------------------------

@login_required
def budget_detail(request, pk):
    budget = get_object_or_404(Budget.objects.select_related("asset", "budget_account"), pk=pk)
    bindings = budget.account_bindings.select_related("ledger_account").order_by("role")
    items = budget.items.select_related("stream_type", "asset").order_by("-created_at")[:50]
    totals = budget_totals(budget)
    from .importers import registry
    importers = [i for i in registry.available()]
    return _render(request, "budget/budget_detail.html", {
        "budget": budget,
        "bindings": bindings,
        "items": items,
        "totals": totals,
        "importers": importers,
    })


# ---------------------------------------------------------------------------
# Budget create / update / delete
# ---------------------------------------------------------------------------

@login_required
def budget_create(request):
    if request.method == "POST":
        form = BudgetForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                budget = form.save(commit=False)
                budget.created_by = request.user
                budget.full_clean()
                budget.save()
            messages.success(request, _("Budget created."))
            return redirect("budget:detail", pk=budget.pk)
    else:
        form = BudgetForm()
    return _render(request, "budget/form.html", {"form": form, "action": _("Create Budget"), "cancel_url": "budget:list"})


@login_required
def budget_update(request, pk):
    budget = get_object_or_404(Budget, pk=pk)
    if request.method == "POST":
        form = BudgetForm(request.POST, instance=budget)
        if form.is_valid():
            with transaction.atomic():
                b = form.save(commit=False)
                b.full_clean()
                b.save()
            messages.success(request, _("Budget updated."))
            return redirect("budget:detail", pk=budget.pk)
    else:
        form = BudgetForm(instance=budget)
    return _render(request, "budget/form.html", {"form": form, "budget": budget, "action": _("Edit Budget"), "cancel_url": "budget:detail"})


@login_required
def budget_delete(request, pk):
    budget = get_object_or_404(Budget, pk=pk)
    if request.method == "POST":
        budget.delete()
        messages.success(request, _("Budget deleted."))
        return redirect("budget:list")
    return _render(request, "budget/confirm_delete.html", {"object": budget, "cancel_url": "budget:detail"})


# ---------------------------------------------------------------------------
# BudgetLedgerAccount
# ---------------------------------------------------------------------------

@login_required
def budget_account_create(request, pk):
    budget = get_object_or_404(Budget, pk=pk)
    if request.method == "POST":
        form = BudgetLedgerAccountForm(request.POST, budget=budget)
        if form.is_valid():
            with transaction.atomic():
                binding = form.save(commit=False)
                binding.budget = budget
                binding.created_by = request.user
                binding.full_clean()
                binding.save()
            messages.success(request, _("Account binding added."))
            return redirect("budget:detail", pk=budget.pk)
    else:
        form = BudgetLedgerAccountForm(budget=budget)
    return _render(request, "budget/form.html", {
        "form": form, "budget": budget,
        "action": _("Add Account Binding"),
        "cancel_url": "budget:detail",
    })


@login_required
def budget_account_update(request, pk):
    binding = get_object_or_404(BudgetLedgerAccount.objects.select_related("budget"), pk=pk)
    if request.method == "POST":
        form = BudgetLedgerAccountForm(request.POST, instance=binding, budget=binding.budget)
        if form.is_valid():
            with transaction.atomic():
                b = form.save(commit=False)
                b.full_clean()
                b.save()
            messages.success(request, _("Account binding updated."))
            return redirect("budget:detail", pk=binding.budget_id)
    else:
        form = BudgetLedgerAccountForm(instance=binding, budget=binding.budget)
    return _render(request, "budget/form.html", {
        "form": form, "budget": binding.budget,
        "action": _("Edit Account Binding"),
        "cancel_url": "budget:detail",
    })


@login_required
def budget_account_delete(request, pk):
    binding = get_object_or_404(BudgetLedgerAccount.objects.select_related("budget"), pk=pk)
    budget_pk = binding.budget_id
    if request.method == "POST":
        binding.delete()
        messages.success(request, _("Account binding removed."))
        return redirect("budget:detail", pk=budget_pk)
    return _render(request, "budget/confirm_delete.html", {"object": binding, "cancel_url": "budget:detail"})


# ---------------------------------------------------------------------------
# BudgetItem
# ---------------------------------------------------------------------------

@login_required
def item_create(request, pk):
    budget = get_object_or_404(Budget, pk=pk)
    if request.method == "POST":
        form = BudgetItemForm(request.POST, budget=budget)
        if form.is_valid():
            with transaction.atomic():
                item = form.save(commit=False)
                item.budget = budget
                item.created_by = request.user
                if not item.asset_id:
                    item.asset = budget.asset
                item.full_clean()
                item.save()
            messages.success(request, _("Item added."))
            return redirect("budget:detail", pk=budget.pk)
    else:
        form = BudgetItemForm(budget=budget, initial={"asset": budget.asset})
    return _render(request, "budget/form.html", {
        "form": form, "budget": budget,
        "action": _("Add Budget Item"),
        "cancel_url": "budget:detail",
    })


@login_required
def item_update(request, pk):
    item = get_object_or_404(BudgetItem.objects.select_related("budget"), pk=pk)
    if request.method == "POST":
        form = BudgetItemForm(request.POST, instance=item, budget=item.budget)
        if form.is_valid():
            with transaction.atomic():
                i = form.save(commit=False)
                i.full_clean()
                i.save()
            messages.success(request, _("Item updated."))
            return redirect("budget:detail", pk=item.budget_id)
    else:
        form = BudgetItemForm(instance=item, budget=item.budget)
    return _render(request, "budget/form.html", {
        "form": form, "budget": item.budget,
        "action": _("Edit Budget Item"),
        "cancel_url": "budget:detail",
    })


@login_required
def item_delete(request, pk):
    item = get_object_or_404(BudgetItem.objects.select_related("budget"), pk=pk)
    budget_pk = item.budget_id
    if request.method == "POST":
        item.delete()
        messages.success(request, _("Item deleted."))
        return redirect("budget:detail", pk=budget_pk)
    return _render(request, "budget/confirm_delete.html", {"object": item, "cancel_url": "budget:detail"})


# ---------------------------------------------------------------------------
# StreamType
# ---------------------------------------------------------------------------

@login_required
def stream_type_list(request):
    qs = BudgetStreamType.objects.all()
    direction_filter = request.GET.get("direction", "").strip()
    active_filter = request.GET.get("active", "").strip()
    q = request.GET.get("q", "").strip()
    if direction_filter:
        qs = qs.filter(direction=direction_filter)
    if active_filter == "1":
        qs = qs.filter(is_active=True)
    elif active_filter == "0":
        qs = qs.filter(is_active=False)
    if q:
        qs = qs.filter(name__icontains=q) | qs.filter(code__icontains=q) if q else qs
    from .models import StreamDirection
    return _render(request, "budget/stream_type_list.html", {
        "stream_types": qs.order_by("sort_order", "code"),
        "direction_filter": direction_filter,
        "active_filter": active_filter,
        "q": q,
        "direction_choices": StreamDirection.choices,
    })


@login_required
def stream_type_create(request):
    if request.method == "POST":
        form = BudgetStreamTypeForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, _("Stream type created."))
            return redirect("budget:stream_type_list")
    else:
        form = BudgetStreamTypeForm()
    return _render(request, "budget/form.html", {
        "form": form, "action": _("Create Stream Type"),
        "cancel_url": "budget:stream_type_list",
    })


@login_required
def stream_type_update(request, pk):
    st = get_object_or_404(BudgetStreamType, pk=pk)
    if request.method == "POST":
        form = BudgetStreamTypeForm(request.POST, instance=st)
        if form.is_valid():
            form.save()
            messages.success(request, _("Stream type updated."))
            return redirect("budget:stream_type_list")
    else:
        form = BudgetStreamTypeForm(instance=st)
    return _render(request, "budget/form.html", {
        "form": form, "action": _("Edit Stream Type"),
        "cancel_url": "budget:stream_type_list",
    })


@login_required
def stream_type_delete(request, pk):
    st = get_object_or_404(BudgetStreamType, pk=pk)
    if request.method == "POST":
        st.delete()
        messages.success(request, _("Stream type deleted."))
        return redirect("budget:stream_type_list")
    return _render(request, "budget/confirm_delete.html", {"object": st, "cancel_url": "budget:stream_type_list"})


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

@login_required
def budget_imports(request, pk):
    budget = get_object_or_404(Budget, pk=pk)
    from .importers import registry
    importers = registry.available()
    importer_info = []
    for imp in importers:
        try:
            count = imp.candidate_count(budget)
        except Exception:
            count = 0
        importer_info.append({"importer": imp, "candidate_count": count})
    return _render(request, "budget/imports.html", {
        "budget": budget,
        "importer_info": importer_info,
    })


@login_required
def budget_import_preview(request, pk, source):
    budget = get_object_or_404(Budget, pk=pk)
    from .importers import registry
    importer = registry.get(source)
    if not importer or not importer.is_available():
        messages.error(request, _("Importer not available."))
        return redirect("budget:imports", pk=pk)
    try:
        candidates = importer.list_candidates(budget=budget)
    except Exception as exc:
        messages.error(request, str(exc))
        candidates = []
    existing_keys = set(
        budget.items.filter(import_key__in=[c.import_key for c in candidates])
        .values_list("import_key", flat=True)
    )
    return _render(request, "budget/import_preview.html", {
        "budget": budget,
        "importer": importer,
        "candidates": candidates,
        "existing_keys": existing_keys,
        "new_count": sum(1 for c in candidates if c.import_key not in existing_keys),
    })


@login_required
def budget_import_run(request, pk, source):
    if request.method != "POST":
        return redirect("budget:import_preview", pk=pk, source=source)
    budget = get_object_or_404(Budget, pk=pk)
    from .importers import registry
    importer = registry.get(source)
    if not importer or not importer.is_available():
        messages.error(request, _("Importer not available."))
        return redirect("budget:imports", pk=pk)
    try:
        items = import_budget_candidates(budget=budget, importer=importer, created_by=request.user)
        messages.success(request, _("%(n)d items imported/updated.") % {"n": len(items)})
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect("budget:detail", pk=pk)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@login_required
def budget_metrics_view(request, pk):
    budget = get_object_or_404(Budget, pk=pk)
    metrics = budget_metrics(budget)
    from .importers import registry
    importers = registry.available()
    pending_counts = {}
    for imp in importers:
        try:
            total = imp.candidate_count(budget)
            existing = budget.items.filter(
                source_type=imp.code
            ).count()
            pending_counts[imp.code] = {"label": imp.label, "total": total}
        except Exception:
            pass
    return _render(request, "budget/metrics.html", {
        "budget": budget,
        "metrics": metrics,
        "pending_counts": pending_counts,
    })


# ---------------------------------------------------------------------------
# JSON endpoints
# ---------------------------------------------------------------------------

@login_required
def budget_summary_data(request, pk):
    budget = get_object_or_404(Budget, pk=pk)
    return JsonResponse(_dec(budget_totals(budget)))


@login_required
def budget_flow_data_view(request, pk):
    budget = get_object_or_404(Budget, pk=pk)
    return JsonResponse(budget_flow_data(budget))
