"""
Budget services — import, aggregate, compute.

These services pull financial consequences from other apps.
They do NOT post ledger transactions, mutate AssetHolding, or create obligations.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


# ---------------------------------------------------------------------------
# upsert_imported_budget_item
# ---------------------------------------------------------------------------

def upsert_imported_budget_item(*, budget, candidate, created_by=None):
    """
    Create or update a BudgetItem from a BudgetImportCandidate.
    Idempotent via (budget, import_key).
    """
    from toto.treasury.models import BudgetItem, BudgetStreamType

    if candidate.asset_id != budget.asset_id:
        raise ValidationError({
            "asset": _(
                f"Candidate asset {candidate.asset_id} does not match "
                f"budget asset {budget.asset_id}."
            )
        })

    try:
        stream_type = BudgetStreamType.objects.get(code=candidate.stream_type_code)
    except BudgetStreamType.DoesNotExist:
        raise ValidationError({
            "stream_type": _(f"Stream type '{candidate.stream_type_code}' not found.")
        })

    defaults = {
        "title": candidate.title,
        "description": candidate.description,
        "stream_type": stream_type,
        "asset_id": candidate.asset_id,
        "amount_base_units": candidate.amount_base_units,
        "status": candidate.status,
        "source_type": candidate.source_type,
        "source_id": str(candidate.source_id) if candidate.source_id else "",
        "source_label": candidate.source_label,
        "source_url": candidate.source_url,
        "due_at": candidate.due_at,
        "booked_at": candidate.booked_at,
        "imported_at": timezone.now(),
        "metadata": candidate.metadata or {},
    }

    # Optional FK references
    if candidate.ledger_transaction_id is not None:
        defaults["ledger_transaction_id"] = candidate.ledger_transaction_id
    if candidate.obligation_id is not None:
        defaults["obligation_id"] = candidate.obligation_id
    if candidate.allocation_id is not None:
        defaults["allocation_id"] = candidate.allocation_id
    if candidate.contract_id is not None:
        defaults["contract_id"] = candidate.contract_id
    if candidate.account_binding_id is not None:
        defaults["account_binding_id"] = candidate.account_binding_id
    if candidate.counterparty_id is not None:
        defaults["counterparty_id"] = candidate.counterparty_id

    item, created = BudgetItem.objects.update_or_create(
        budget=budget,
        import_key=candidate.import_key,
        defaults=defaults,
    )
    if created and created_by:
        item.created_by = created_by
        item.save(update_fields=["created_by"])

    item.full_clean()
    return item


# ---------------------------------------------------------------------------
# import_budget_candidates
# ---------------------------------------------------------------------------

def import_budget_candidates(*, budget, importer, created_by=None) -> list:
    """Run an importer against a budget, upsert all candidates. Return items list."""
    candidates = importer.list_candidates(budget=budget)
    items = []
    for candidate in candidates:
        try:
            item = upsert_imported_budget_item(
                budget=budget,
                candidate=candidate,
                created_by=created_by,
            )
            items.append(item)
        except (ValidationError, Exception):
            pass
    return items


# ---------------------------------------------------------------------------
# budget_totals
# ---------------------------------------------------------------------------

def budget_totals(budget) -> dict:
    """Compute aggregate totals for a budget. Excludes cancelled/reversed."""
    from toto.treasury.models import BudgetItem, BudgetItemStatus, StreamDirection

    active_qs = budget.items.exclude(
        status__in=[BudgetItemStatus.CANCELLED, BudgetItemStatus.REVERSED]
    )
    booked_qs = active_qs.filter(status=BudgetItemStatus.BOOKED)
    committed_qs = active_qs.filter(status=BudgetItemStatus.COMMITTED)
    planned_qs = active_qs.filter(
        status__in=[BudgetItemStatus.PLANNED, BudgetItemStatus.APPROVED]
    )

    def _sum(qs, direction):
        return qs.filter(stream_type__direction=direction).aggregate(
            total=Sum("amount_base_units")
        )["total"] or 0

    return {
        "total_inflow": _sum(active_qs, StreamDirection.INFLOW),
        "total_outflow": _sum(active_qs, StreamDirection.OUTFLOW),
        "net": _sum(active_qs, StreamDirection.INFLOW) - _sum(active_qs, StreamDirection.OUTFLOW),
        "booked_inflow": _sum(booked_qs, StreamDirection.INFLOW),
        "booked_outflow": _sum(booked_qs, StreamDirection.OUTFLOW),
        "booked_net": _sum(booked_qs, StreamDirection.INFLOW) - _sum(booked_qs, StreamDirection.OUTFLOW),
        "committed_outflow": _sum(committed_qs, StreamDirection.OUTFLOW),
        "planned_inflow": _sum(planned_qs, StreamDirection.INFLOW),
        "planned_outflow": _sum(planned_qs, StreamDirection.OUTFLOW),
    }


# ---------------------------------------------------------------------------
# budget_metrics
# ---------------------------------------------------------------------------

def budget_metrics(budget) -> dict:
    """Compute detailed metrics for a budget."""
    from toto.treasury.models import BudgetItem, BudgetItemStatus, StreamDirection
    from django.db.models.functions import TruncMonth

    qs = budget.items.exclude(
        status__in=[BudgetItemStatus.CANCELLED, BudgetItemStatus.REVERSED]
    ).select_related("stream_type")

    by_stream = (
        qs.values("stream_type__code", "stream_type__name", "stream_type__direction")
        .annotate(total=Sum("amount_base_units"))
        .order_by("-total")
    )

    by_source = (
        qs.exclude(source_type="")
        .values("source_type")
        .annotate(total=Sum("amount_base_units"))
        .order_by("-total")
    )

    by_status = (
        budget.items.values("status")
        .annotate(total=Sum("amount_base_units"))
        .order_by("status")
    )

    monthly = (
        qs.filter(booked_at__isnull=False)
        .annotate(month=TruncMonth("booked_at"))
        .values("month", "stream_type__direction")
        .annotate(total=Sum("amount_base_units"))
        .order_by("month")
    )

    recent = budget.items.select_related("stream_type").order_by("-imported_at")[:20]

    return {
        "totals": budget_totals(budget),
        "by_stream_type": list(by_stream),
        "by_source_type": list(by_source),
        "by_status": list(by_status),
        "monthly_trend": list(monthly),
        "recent_items": recent,
    }


# ---------------------------------------------------------------------------
# budget_flow_data — Sankey JSON
# ---------------------------------------------------------------------------

def budget_flow_data(budget) -> dict:
    """
    Return Sankey-ready nodes/edges for a budget.
    Central node = budget. Inflows flow in, outflows flow out.
    """
    from toto.treasury.models import BudgetItem, BudgetItemStatus, StreamDirection

    qs = budget.items.exclude(
        status__in=[BudgetItemStatus.CANCELLED, BudgetItemStatus.REVERSED]
    ).select_related("stream_type")

    budget_node_id = f"treasury:{budget.pk}"
    nodes_map = {budget_node_id: {"id": budget_node_id, "label": budget.name, "kind": "budget"}}
    edges_agg: dict[tuple, int] = {}

    for item in qs:
        st = item.stream_type
        if st.direction == StreamDirection.INFLOW:
            src_label = item.source_label or item.source_type or "Unknown"
            src_id = f"src:{item.source_type}:{src_label}"
            if src_id not in nodes_map:
                nodes_map[src_id] = {"id": src_id, "label": src_label, "kind": "source"}
            key = (src_id, budget_node_id, st.code)
        else:
            tgt_id = f"st:{st.code}"
            if tgt_id not in nodes_map:
                nodes_map[tgt_id] = {"id": tgt_id, "label": st.name, "kind": "stream_type"}
            key = (budget_node_id, tgt_id, st.code)

        edges_agg[key] = edges_agg.get(key, 0) + item.amount_base_units

    edges = [
        {"source": src, "target": tgt, "label": label, "value": val}
        for (src, tgt, label), val in edges_agg.items()
        if val > 0
    ]

    return {"nodes": list(nodes_map.values()), "edges": edges}
