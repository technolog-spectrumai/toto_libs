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


def fee_history_chart_data() -> str:
    """
    Returns Chart.js-ready JSON for a scatter/line chart of fee_bps over time,
    grouped by asset (or 'blanket').
    """
    fees = (
        CommunityTransactionFee.objects
        .select_related("asset", "community")
        .order_by("created_at")
    )

    series: dict[str, list[dict]] = defaultdict(list)
    for fee in fees:
        label = f"{fee.community.name} — {fee.asset.unit_name if fee.asset else 'blanket'}"
        series[label].append({
            "x": fee.created_at.strftime("%Y-%m-%d"),
            "y": fee.fee_bps,
        })

    datasets = []
    palette = [
        "#6366f1", "#f59e0b", "#10b981", "#ef4444", "#3b82f6",
        "#8b5cf6", "#ec4899", "#14b8a6", "#f97316", "#84cc16",
    ]
    for i, (label, points) in enumerate(series.items()):
        color = palette[i % len(palette)]
        datasets.append({
            "label": label,
            "data": points,
            "borderColor": color,
            "backgroundColor": color + "33",
            "tension": 0.3,
            "pointRadius": 4,
        })

    return json.dumps({"datasets": datasets})
