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
    communities = Community.objects.all().order_by("name")
    rows = []
    for community in communities:
        account = get_treasury_account(community)
        balances = []
        if account:
            for holding in account.holdings.select_related("asset").order_by("-balance_base_units"):
                if holding.balance_base_units != 0:
                    balances.append({
                        "unit": holding.asset.unit_name,
                        "balance": holding.balance_display,
                        "decimals": holding.asset.decimals,
                    })
        rows.append({
            "community": community,
            "account": account,
            "balances": balances,
        })
    return _render(request, "treasury/community_list.html", {"rows": rows})


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
