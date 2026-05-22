from __future__ import annotations

import json
from collections import defaultdict

from django.db.models import Count, Max, Prefetch, Q

from .models import (
    AssemblyDecision,
    AssemblyProposal,
    AssemblyProposalType,
    AssemblyStatus,
    CommunityRule,
    CommunityTransactionFee,
    PollTax,
)


def overview_stats() -> dict:
    return {
        "communities_with_proposals": AssemblyProposal.objects.values("community").distinct().count(),
        "open_proposals": AssemblyProposal.objects.filter(status=AssemblyStatus.OPEN).count(),
        "total_decisions": AssemblyDecision.objects.count(),
        "active_rules": CommunityRule.objects.filter(active=True).count(),
        "active_fees": CommunityTransactionFee.objects.filter(active=True).count(),
        "active_poll_taxes": PollTax.objects.filter(active=True).count(),
        "rule_proposals": AssemblyProposal.objects.filter(proposal_type=AssemblyProposalType.RULE).count(),
        "tax_proposals": AssemblyProposal.objects.filter(proposal_type=AssemblyProposalType.ASSET_TAX).count(),
    }


def community_assembly_summaries():
    """
    Return communities that have at least one proposal, annotated with
    recent rules, recent decisions, and open proposal count.
    """
    from toto.socialhub.models import Community

    communities = (
        Community.objects
        .annotate(
            open_proposal_count=Count(
                "assembly_proposals",
                filter=Q(assembly_proposals__status=AssemblyStatus.OPEN),
            ),
            latest_decision_at=Max("assembly_decisions__created_at"),
        )
        .filter(assembly_proposals__isnull=False)
        .distinct()
        .order_by("-latest_decision_at")
        .prefetch_related(
            Prefetch(
                "rules",
                queryset=CommunityRule.objects.filter(active=True).order_by("-created_at")[:3],
                to_attr="recent_rules",
            ),
            Prefetch(
                "assembly_decisions",
                queryset=AssemblyDecision.objects.order_by("-created_at")[:3],
                to_attr="recent_decisions",
            ),
        )[:20]
    )
    return communities


PALETTE = [
    "#6366f1", "#f59e0b", "#10b981", "#ef4444", "#3b82f6",
    "#8b5cf6", "#ec4899", "#14b8a6", "#f97316", "#84cc16",
]


def fee_current_chart_data() -> str:
    """Bar chart of all active fees (current snapshot)."""
    fees = list(
        CommunityTransactionFee.objects
        .filter(active=True)
        .select_related("asset", "community")
        .order_by("community__name", "asset__unit_name")
    )
    if not fees:
        return json.dumps({"labels": [], "datasets": []})

    labels = [
        f"{f.community.name} — {f.asset.unit_name if f.asset else 'blanket'}"
        for f in fees
    ]
    colors = [PALETTE[i % len(PALETTE)] for i in range(len(fees))]
    return json.dumps({
        "labels": labels,
        "datasets": [{
            "label": "Fee (bps)",
            "data": [f.fee_bps for f in fees],
            "backgroundColor": colors,
            "borderColor": colors,
            "borderWidth": 1,
            "borderRadius": 6,
        }],
    })


def fee_history_chart_data() -> str:
    """
    Line chart of fee changes over time, one series per community+asset.
    Uses explicit labels (sorted unique dates) so category scale works correctly.
    Only meaningful when there are >=2 records.
    """
    fees = list(
        CommunityTransactionFee.objects
        .select_related("asset", "community")
        .order_by("created_at")
    )
    if not fees:
        return json.dumps({"labels": [], "datasets": []})

    # Collect sorted unique date labels
    all_dates = sorted({f.created_at.strftime("%Y-%m-%d") for f in fees})

    # Group by series
    series: dict[str, dict[str, int]] = defaultdict(dict)
    for fee in fees:
        key = f"{fee.community.name} — {fee.asset.unit_name if fee.asset else 'blanket'}"
        date = fee.created_at.strftime("%Y-%m-%d")
        series[key][date] = fee.fee_bps

    datasets = []
    for i, (label, date_map) in enumerate(series.items()):
        color = PALETTE[i % len(PALETTE)]
        # Fill value for each label date (None = gap)
        data = [date_map.get(d) for d in all_dates]
        datasets.append({
            "label": label,
            "data": data,
            "borderColor": color,
            "backgroundColor": color + "33",
            "tension": 0.3,
            "pointRadius": 5,
            "spanGaps": True,
        })

    return json.dumps({"labels": all_dates, "datasets": datasets})
