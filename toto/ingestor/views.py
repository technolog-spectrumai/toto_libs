"""Ingestor views — a Ravioli tab: paste text → review graph diff → apply patch.

Page view renders the review surface; the rest are JSON endpoints driving the
Alpine ``ingestorFlow()`` component. All graph access is via the services layer
(Bento/Ravioli) — no Neo4j here.
"""

import json

from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from toto.ui import PageProcessor

from .models import IngestProposal
from .services import apply as apply_svc
from .services import pipeline
from .services.validation import revalidate, summarize


def superuser_required(view_func):
    return user_passes_test(lambda u: u.is_active and u.is_superuser)(view_func)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _bento_templates():
    """Category + edge-type metadata the detail panel needs for its selects."""
    from toto.bento.models import BentoCategory, BentoEdgeType

    categories = [
        {
            "slug": c.slug,
            "name": c.name,
            "property_schema": c.property_schema or [],
        }
        for c in BentoCategory.objects.all()
    ]
    edge_types = [
        {
            "slug": et.slug,
            "name": et.name,
            "rel_type": et.rel_type,
            "property_schema": et.property_schema or [],
            "allowed_sources": list(et.allowed_sources.values_list("slug", flat=True)),
            "allowed_targets": list(et.allowed_targets.values_list("slug", flat=True)),
        }
        for et in BentoEdgeType.objects.prefetch_related("allowed_sources", "allowed_targets")
    ]
    return categories, edge_types


def _payload(proposal_model):
    return {
        "proposal_id": proposal_model.id,
        "status": proposal_model.status,
        "summary": proposal_model.summary,
        "proposal": proposal_model.proposal,
        "error": proposal_model.error,
        "detail_url": reverse("ingestor:proposal_detail", args=[proposal_model.id]),
        "apply_url": reverse("ingestor:apply", args=[proposal_model.id]),
    }


def _save_edits(proposal_model):
    """Re-validate the whole proposal, refresh the summary, persist."""
    revalidate(proposal_model.proposal)
    proposal_model.summary = {**(proposal_model.summary or {}), **summarize(proposal_model.proposal)}
    proposal_model.save(update_fields=["proposal", "summary", "updated_at"])


# ---------------------------------------------------------------------------
# page
# ---------------------------------------------------------------------------

@login_required
@superuser_required
def home(request):
    from toto.ravioli.connection import is_enabled

    categories, edge_types = ([], [])
    if is_enabled():
        try:
            categories, edge_types = _bento_templates()
        except Exception:  # noqa: BLE001 — graph may be down; page still renders
            categories, edge_types = ([], [])

    context = PageProcessor().decorate(
        {
            "categories_json": json.dumps(categories),
            "edge_types_json": json.dumps(edge_types),
            "generate_url": reverse("ingestor:generate"),
        },
        request,
    )
    return render(request, "ingestor/ingestor.html", context)


# ---------------------------------------------------------------------------
# JSON endpoints
# ---------------------------------------------------------------------------

@require_POST
@superuser_required
def generate(request):
    from toto.ravioli.connection import is_enabled

    if not is_enabled():
        return JsonResponse(
            {"error": "RAVIOLI_ENABLED is False — cannot connect to Neo4j."}, status=503
        )
    text = (request.POST.get("text") or "").strip()
    if not text:
        return JsonResponse({"error": "Paste some text first."}, status=400)
    try:
        proposal_model = pipeline.generate(text, user=request.user)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"error": f"Could not build a proposal: {exc}"}, status=500)
    return JsonResponse(_payload(proposal_model))


@require_GET
@superuser_required
def proposal_detail(request, pk):
    proposal_model = get_object_or_404(IngestProposal, pk=pk)
    return JsonResponse(_payload(proposal_model))


def _find(proposal, key, temp_id):
    for element in proposal.get(key, []):
        if element.get("temp_id") == temp_id:
            return element
    return None


@require_POST
@superuser_required
def patch_node(request, pk, temp_id):
    proposal_model = get_object_or_404(IngestProposal, pk=pk)
    if proposal_model.status == IngestProposal.STATUS_APPLIED:
        return JsonResponse({"error": "Proposal already applied."}, status=409)
    node = _find(proposal_model.proposal, "nodes", temp_id)
    if node is None:
        return JsonResponse({"error": "Unknown node."}, status=404)

    try:
        data = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    # Apply only the editable fields.
    if "category_slug" in data:
        node["category_slug"] = data["category_slug"] or None
    if "properties" in data and isinstance(data["properties"], dict):
        node["properties"] = data["properties"]
    if "display" in data:
        node["display"] = data["display"]
    if "approval" in data and data["approval"] in ("pending", "approved", "rejected"):
        node["approval"] = data["approval"]
    if "merge_into_uid" in data:
        uid = data["merge_into_uid"] or None
        node["merge_into_uid"] = uid
        if uid:
            # Merge into an existing node: become a reference, adopt its category.
            node["uid"] = uid
            if data.get("merge_category_slug"):
                node["category_slug"] = data["merge_category_slug"]
            if data.get("merge_display"):
                node["display"] = data["merge_display"]

    _save_edits(proposal_model)
    return JsonResponse({"node": node, "summary": proposal_model.summary})


@require_POST
@superuser_required
def patch_rel(request, pk, temp_id):
    proposal_model = get_object_or_404(IngestProposal, pk=pk)
    if proposal_model.status == IngestProposal.STATUS_APPLIED:
        return JsonResponse({"error": "Proposal already applied."}, status=409)
    rel = _find(proposal_model.proposal, "relationships", temp_id)
    if rel is None:
        return JsonResponse({"error": "Unknown relationship."}, status=404)

    try:
        data = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    if "edge_type_slug" in data and data["edge_type_slug"]:
        from toto.bento.models import BentoEdgeType

        et = BentoEdgeType.objects.filter(slug=data["edge_type_slug"]).first()
        if not et:
            return JsonResponse({"error": "Unknown edge type."}, status=400)
        rel["edge_type_slug"] = et.slug
        rel["rel_type"] = et.rel_type
        rel["edge_type_name"] = et.name
    if "properties" in data and isinstance(data["properties"], dict):
        rel["properties"] = data["properties"]
    if "approval" in data and data["approval"] in ("pending", "approved", "rejected"):
        rel["approval"] = data["approval"]

    _save_edits(proposal_model)
    return JsonResponse({"relationship": rel, "summary": proposal_model.summary})


@require_POST
@superuser_required
def apply(request, pk):
    from toto.ravioli.connection import is_enabled

    if not is_enabled():
        return JsonResponse(
            {"error": "RAVIOLI_ENABLED is False — cannot connect to Neo4j."}, status=503
        )
    proposal_model = get_object_or_404(IngestProposal, pk=pk)
    if proposal_model.status == IngestProposal.STATUS_APPLIED:
        return JsonResponse({"error": "Proposal already applied."}, status=409)
    try:
        _result, errors = apply_svc.run(proposal_model)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"error": f"Apply failed: {exc}"}, status=500)
    payload = _payload(proposal_model)
    payload["errors"] = errors
    return JsonResponse(payload)
