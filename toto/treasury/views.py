"""
Treasury views — read-only financial dashboard for communities.

Reads from: assets.LedgerEntry/LedgerAccount/AssetHolding, socialhub.Community.
No treasury-specific models.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render

from toto.assets.models import Asset, AssetHolding, LedgerEntry
from toto.socialhub.models import Community
from toto.socialhub.treasury import get_treasury_account
from toto.ui import PageProcessor


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _entries_for_account(account):
    return (
        LedgerEntry.objects
        .filter(account=account, transaction__posted=True)
        .select_related("transaction", "asset")
        .order_by("-transaction__created_at")
    )


# ---------------------------------------------------------------------------
# Community list
# ---------------------------------------------------------------------------

@login_required
def community_list(request):
    first = Community.objects.order_by("name").first()
    if first:
        from django.shortcuts import redirect
        return redirect("treasury:detail", pk=first.pk)
    return _render(request, "treasury/community_list.html", {"rows": [], "communities": [], "selected_community": None})


# ---------------------------------------------------------------------------
# Community detail dashboard
# ---------------------------------------------------------------------------

@login_required
def community_detail(request, pk):
    community = get_object_or_404(Community, pk=pk)
    account = get_treasury_account(community)

    if account is None:
        return _render(request, "treasury/no_account.html", {"community": community})

    holdings = list(
        account.holdings.select_related("asset")
        .filter(balance_base_units__gt=0)
        .order_by("-balance_base_units")
    )

    recent_entries = list(
        _entries_for_account(account)
        .select_related("transaction")[:50]
    )

    inflow_total = (
        _entries_for_account(account)
        .filter(amount_base_units__gt=0)
        .aggregate(total=Sum("amount_base_units"))["total"] or 0
    )
    outflow_total = abs(
        _entries_for_account(account)
        .filter(amount_base_units__lt=0)
        .aggregate(total=Sum("amount_base_units"))["total"] or 0
    )

    return _render(request, "treasury/community_detail.html", {
        "community": community,
        "account": account,
        "holdings": holdings,
        "recent_entries": recent_entries,
        "inflow_total": inflow_total,
        "outflow_total": outflow_total,
        "net": inflow_total - outflow_total,
    })


# ---------------------------------------------------------------------------
# Sankey flow JSON
# ---------------------------------------------------------------------------

@login_required
def community_flow_json(request, pk):
    community = get_object_or_404(Community, pk=pk)
    account = get_treasury_account(community)

    if account is None:
        return JsonResponse({"nodes": [], "edges": []})

    entries = (
        _entries_for_account(account)
        .select_related("transaction", "asset")
    )

    account_node = {"id": "treasury", "label": f"{community.name} Treasury", "kind": "treasury"}
    nodes_map = {"treasury": account_node}
    edges_agg: dict[tuple, int] = {}

    for entry in entries:
        src_type = entry.transaction.source_type or entry.transaction.transaction_type or "other"
        label = src_type.replace(".", " ").replace("_", " ").title()
        if entry.amount_base_units > 0:
            node_id = f"in:{src_type}"
            if node_id not in nodes_map:
                nodes_map[node_id] = {"id": node_id, "label": label, "kind": "inflow"}
            key = (node_id, "treasury", src_type)
            edges_agg[key] = edges_agg.get(key, 0) + entry.amount_base_units
        else:
            node_id = f"out:{src_type}"
            if node_id not in nodes_map:
                nodes_map[node_id] = {"id": node_id, "label": label, "kind": "outflow"}
            key = ("treasury", node_id, src_type)
            edges_agg[key] = edges_agg.get(key, 0) + abs(entry.amount_base_units)

    edges = [
        {"source": src, "target": tgt, "label": lbl, "value": val}
        for (src, tgt, lbl), val in edges_agg.items()
        if val > 0
    ]

    return JsonResponse({"nodes": list(nodes_map.values()), "edges": edges})


# ---------------------------------------------------------------------------
# Balance history JSON
# ---------------------------------------------------------------------------

@login_required
def community_history_json(request, pk):
    community = get_object_or_404(Community, pk=pk)
    account = get_treasury_account(community)

    if account is None:
        return JsonResponse({"history": []})

    monthly = (
        LedgerEntry.objects
        .filter(account=account, transaction__posted=True)
        .annotate(month=TruncMonth("transaction__created_at"))
        .values("month", "asset__unit_name")
        .annotate(net=Sum("amount_base_units"))
        .order_by("month")
    )

    by_asset: dict[str, list] = defaultdict(list)
    for row in monthly:
        by_asset[row["asset__unit_name"]].append({
            "month": row["month"].strftime("%Y-%m") if row["month"] else None,
            "net": row["net"],
        })

    return JsonResponse({"history": dict(by_asset)})


# ---------------------------------------------------------------------------
# Revenue dashboard — combines tariffs + subscriptions + taxes streams
# ---------------------------------------------------------------------------

@login_required
def revenue_dashboard(request):
    import json as _json
    from django.apps import apps as django_apps
    from django.utils import timezone as tz

    cutoff = tz.now() - tz.timedelta(days=395)

    def _monthly(qs, date_field, amount_field, label_field=None):
        """Aggregate a queryset into {month_str: total} dicts, grouped by optional label."""
        from django.db.models.functions import TruncMonth
        annotated = (
            qs.annotate(month=TruncMonth(date_field))
            .values("month", *([label_field] if label_field else []))
            .annotate(total=Sum(amount_field))
            .order_by("month")
        )
        result = defaultdict(lambda: defaultdict(int))
        for row in annotated:
            m = row["month"].strftime("%Y-%m") if row["month"] else "?"
            label = row[label_field] if label_field else "total"
            result[label][m] += row["total"] or 0
        return dict(result)

    streams = {}

    # ── Tariff income ─────────────────────────────────────────────────────────
    if django_apps.is_installed("toto.tariffs"):
        from toto.tariffs.models import UsageCharge
        qs = UsageCharge.objects.filter(usage_record__occurred_at__gte=cutoff)
        for unit_name, monthly in _monthly(
            qs, "usage_record__occurred_at", "amount_base_units",
            label_field="charged_asset__unit_name",
        ).items():
            streams[f"Tariff — {unit_name or '?'}"] = monthly

    # ── Subscription income ───────────────────────────────────────────────────
    if django_apps.is_installed("toto.subscriptions"):
        from toto.subscriptions.models import SubscriptionPayment
        qs = SubscriptionPayment.objects.filter(
            status="succeeded", created_at__gte=cutoff
        )
        for asset_code, monthly in _monthly(
            qs, "created_at", "amount_base_units",
            label_field="invoice__price__asset__unit_name",
        ).items():
            streams[f"Subscriptions — {asset_code or '?'}"] = monthly

    # ── Tax income ────────────────────────────────────────────────────────────
    if django_apps.is_installed("toto.taxes"):
        from toto.taxes.models import TransactionFee
        qs = TransactionFee.objects.filter(created_at__gte=cutoff)
        for unit_name, monthly in _monthly(
            qs, "created_at", "amount_base_units",
            label_field="asset__unit_name",
        ).items():
            streams[f"Taxes — {unit_name or '?'}"] = monthly

    # ── Build Chart.js datasets ───────────────────────────────────────────────
    all_months = sorted({m for monthly in streams.values() for m in monthly})

    def _color(i):
        palette = [
            "rgb(99,102,241)", "rgb(20,184,166)", "rgb(245,158,11)",
            "rgb(239,68,68)", "rgb(34,197,94)", "rgb(168,85,247)",
            "rgb(249,115,22)", "rgb(14,165,233)", "rgb(236,72,153)",
        ]
        return palette[i % len(palette)]

    datasets = []
    for i, (label, monthly) in enumerate(sorted(streams.items())):
        color = _color(i)
        data = [monthly.get(m, 0) for m in all_months]
        datasets.append({
            "label": label,
            "data": data,
            "borderColor": color,
            "backgroundColor": color.replace("rgb(", "rgba(").replace(")", ", 0.12)"),
            "tension": 0.3,
            "fill": True,
        })

    # Total series
    if len(datasets) > 1:
        totals = [sum(ds["data"][i] for ds in datasets) for i in range(len(all_months))]
        datasets.append({
            "label": "Total",
            "data": totals,
            "borderColor": "rgb(107,114,128)",
            "backgroundColor": "transparent",
            "borderDash": [6, 3],
            "tension": 0.3,
            "fill": False,
            "order": -1,
        })

    # ── Projection (linear, 3 months) ─────────────────────────────────────────
    def _project(series, n_history=3, n_future=3):
        if len(series) < 2:
            return [max(0, series[-1]) if series else 0] * n_future
        tail = series[-n_history:]
        xs = list(range(len(tail)))
        n = len(xs)
        mean_x, mean_y = sum(xs) / n, sum(tail) / n
        denom = sum((x - mean_x) ** 2 for x in xs) or 1
        slope = sum((xs[j] - mean_x) * (tail[j] - mean_y) for j in range(n)) / denom
        last = tail[-1]
        return [max(0, last + slope * (k + 1)) for k in range(n_future)]

    from datetime import datetime as _dt, timedelta as _td
    last_month = _dt.strptime(all_months[-1], "%Y-%m") if all_months else _dt.now()
    future_months = [
        (last_month.replace(day=1) + _td(days=32 * (k + 1))).strftime("%Y-%m")
        for k in range(3)
    ]
    proj_datasets = [
        {
            "label": f"{ds['label']} (proj.)",
            "data": _project(ds["data"]),
            "borderColor": ds["borderColor"],
            "backgroundColor": "transparent",
            "borderDash": [4, 4],
            "tension": 0.3,
            "fill": False,
        }
        for ds in datasets
    ]

    # ── Summary totals ────────────────────────────────────────────────────────
    total_revenue = sum(
        v for monthly in streams.values() for v in monthly.values()
    )

    return _render(request, "treasury/revenue_dashboard.html", {
        "chart_labels": _json.dumps(all_months),
        "chart_datasets": _json.dumps(datasets),
        "future_months": _json.dumps(future_months),
        "proj_datasets": _json.dumps(proj_datasets),
        "streams": list(streams.keys()),
        "total_revenue": total_revenue,
        "has_data": bool(all_months),
    })


# ---------------------------------------------------------------------------
# Taxes overview
# ---------------------------------------------------------------------------

@login_required
def taxes_overview(request):
    from django.apps import apps as django_apps

    if not django_apps.is_installed("toto.taxes"):
        return _render(request, "treasury/taxes_overview.html", {
            "policies": [],
            "recent_fees": [],
            "taxes_enabled": False,
        })

    from toto.taxes.models import TransactionFee, TransactionFeePolicy

    policies = list(
        TransactionFeePolicy.objects
        .select_related("asset", "receiving_account")
        .order_by("asset__unit_name", "name")
    )
    recent_fees = list(
        TransactionFee.objects
        .select_related("policy", "asset", "transaction")
        .order_by("-created_at")[:50]
    )
    total_collected = TransactionFee.objects.aggregate(
        total=Sum("amount_base_units")
    )["total"] or 0

    return _render(request, "treasury/taxes_overview.html", {
        "policies": policies,
        "recent_fees": recent_fees,
        "total_collected": total_collected,
        "taxes_enabled": True,
    })
