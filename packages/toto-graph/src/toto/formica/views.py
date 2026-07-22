"""Formica control panel — dashboard Ops card → /formica/ (superuser-only).

Pages render colony state from SQL; graph reads go through
``colony.graph_ops`` (formica's sole Neo4j surface) and are always wrapped so
a downed graph degrades to empty panels + the shared ravioli warning banner.
"""

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from toto.ui import PageProcessor

from . import params as params_mod
from .colony.castes import caste_choices
from .models import CasteConfig, Colony, ColonyCycle, FormicaProposal


def superuser_required(view_func):
    return user_passes_test(lambda u: u.is_active and u.is_superuser)(view_func)


def _colony():
    return Colony.objects.first()


def _decorate(request, context):
    return PageProcessor().decorate(context, request)


def _graph_ops_readonly():
    from .colony.graph_ops import GraphOps

    return GraphOps(write_budget=0, cycle_number=0)


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------

@login_required
@superuser_required
def overview(request):
    from toto.celery_utils import celery_available

    colony = _colony()
    last_cycle = colony.cycles.exclude(
        status=ColonyCycle.STATUS_PENDING
    ).first() if colony else None
    running = colony.has_cycle_in_flight() if colony else False
    pending_proposals = (
        colony.proposals.filter(status=FormicaProposal.STATUS_READY).count()
        if colony else 0
    )
    stats = (last_cycle.stats or {}) if last_cycle else {}
    kpis = {
        "nodes_visited": (stats.get("castes", {}).get("forager", {}) or {}).get(
            "nodes_visited", 0
        ),
        "ph_mass": stats.get("ph_mass", 0),
        "quarantined": stats.get("quarantined", 0),
        "builds": (stats.get("castes", {}).get("architect", {}) or {}).get(
            "builds_proposed", 0
        ),
        "prunes": (stats.get("castes", {}).get("pruner", {}) or {}).get(
            "deletes_proposed", 0
        ),
    }
    context = _decorate(request, {
        "colony": colony,
        "last_cycle": last_cycle,
        "cycle_running": running,
        "pending_proposals": pending_proposals,
        "kpis": kpis,
        "celery_up": celery_available(),
        "active_tab": "overview",
    })
    return render(request, "formica/overview.html", context)


@login_required
@superuser_required
def params_view(request):
    colony = _colony()
    if colony is None:
        messages.warning(request, "No colony yet — run `manage.py ingress_formica`.")
        return redirect("formica:overview")

    if request.method == "POST":
        if request.POST.get("reset"):
            colony.meta_params = {}
            colony.save(update_fields=["meta_params", "updated_at"])
            messages.success(request, "Parameters reset to defaults.")
            return redirect("formica:params")
        new_params, errors = _parse_params(request.POST)
        try:
            colony.interval_minutes = max(int(request.POST.get("interval_minutes", colony.interval_minutes)), 1)
        except (TypeError, ValueError):
            errors.append("interval_minutes must be an integer.")
        errors += params_mod.validate_params(new_params)
        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            colony.meta_params = new_params
            colony.save(update_fields=["meta_params", "interval_minutes", "updated_at"])
            _save_caste_configs(request, colony)
            messages.success(request, "Colony parameters saved.")
            return redirect("formica:params")

    resolved = colony.params()
    groups = []
    for group in params_mod.PARAM_GROUPS:
        specs = [
            {"name": name, "value": resolved[name], **spec}
            for name, spec in params_mod.PARAM_SPECS.items()
            if spec["group"] == group
        ]
        groups.append({"key": group, "specs": specs})
    caste_rows = []
    configured = {c.caste_key: c for c in colony.castes.all()}
    for choice in caste_choices():
        config = configured.get(choice["key"])
        caste_rows.append({**choice, "config": config})
    context = _decorate(request, {
        "colony": colony,
        "groups": groups,
        "caste_rows": caste_rows,
        "active_tab": "params",
    })
    return render(request, "formica/params.html", context)


def _parse_params(post):
    new_params, errors = {}, []
    for name, spec in params_mod.PARAM_SPECS.items():
        raw = (post.get(f"param_{name}") or "").strip()
        if raw == "":
            continue
        try:
            value = int(raw) if spec["type"] is int else float(raw)
        except ValueError:
            errors.append(f"'{name}' must be a number.")
            continue
        if value != spec["default"]:
            new_params[name] = value
    return new_params, errors


def _save_caste_configs(request, colony):
    for choice in caste_choices():
        key = choice["key"]
        config, _created = CasteConfig.objects.get_or_create(
            colony=colony, caste_key=key
        )
        config.enabled = bool(request.POST.get(f"caste_{key}_enabled"))
        try:
            config.ant_count = max(int(request.POST.get(f"caste_{key}_ants", config.ant_count)), 0)
        except (TypeError, ValueError):
            pass
        config.save()


@login_required
@superuser_required
def cycles(request):
    colony = _colony()
    queryset = colony.cycles.all() if colony else ColonyCycle.objects.none()
    paginator = Paginator(queryset, 20)
    page_obj = paginator.get_page(request.GET.get("page"))
    context = _decorate(request, {
        "colony": colony,
        "page_obj": page_obj,
        "is_paginated": page_obj.has_other_pages(),
        "active_tab": "cycles",
    })
    return render(request, "formica/cycles.html", context)


@login_required
@superuser_required
def pheromone_map(request):
    context = _decorate(request, {
        "colony": _colony(),
        "active_tab": "map",
    })
    return render(request, "formica/map.html", context)


@login_required
@superuser_required
def proposals(request):
    colony = _colony()
    queryset = (
        colony.proposals.select_related("cycle") if colony
        else FormicaProposal.objects.none()
    )
    paginator = Paginator(queryset, 20)
    page_obj = paginator.get_page(request.GET.get("page"))
    context = _decorate(request, {
        "colony": colony,
        "page_obj": page_obj,
        "is_paginated": page_obj.has_other_pages(),
        "active_tab": "proposals",
    })
    return render(request, "formica/proposals.html", context)


@login_required
@superuser_required
def proposal_detail(request, pk):
    proposal = get_object_or_404(
        FormicaProposal.objects.select_related("colony", "cycle"), pk=pk
    )
    context = _decorate(request, {
        "colony": proposal.colony,
        "proposal": proposal,
        "ops_list": proposal.op_list,
        "editable": proposal.status
        in (FormicaProposal.STATUS_READY, FormicaProposal.STATUS_FAILED),
        "active_tab": "proposals",
    })
    return render(request, "formica/proposal_detail.html", context)


# ---------------------------------------------------------------------------
# JSON endpoints
# ---------------------------------------------------------------------------

@require_POST
@superuser_required
def api_run_now(request):
    from .tasks import CycleInProgress, trigger_cycle

    colony = _colony()
    if colony is None:
        return JsonResponse({"error": "No colony configured."}, status=404)
    from toto.ravioli.connection import is_enabled

    if not is_enabled():
        return JsonResponse(
            {"error": "RAVIOLI_ENABLED is False — cannot reach Neo4j."}, status=503
        )
    try:
        cycle, queued = trigger_cycle(colony, triggered_by=ColonyCycle.TRIGGERED_MANUAL)
    except CycleInProgress as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    return JsonResponse({
        "cycle_id": cycle.pk,
        "number": cycle.number,
        "queued": queued,
        "status": cycle.status if queued else "completed",
    })


@require_POST
@superuser_required
def api_pause(request):
    colony = _colony()
    if colony is None:
        return JsonResponse({"error": "No colony configured."}, status=404)
    colony.paused = not colony.paused
    colony.save(update_fields=["paused", "updated_at"])
    return JsonResponse({"paused": colony.paused})


@require_POST
@superuser_required
def api_trusted(request):
    colony = _colony()
    if colony is None:
        return JsonResponse({"error": "No colony configured."}, status=404)
    colony.trusted = not colony.trusted
    colony.save(update_fields=["trusted", "updated_at"])
    return JsonResponse({"trusted": colony.trusted})


@require_GET
@superuser_required
def api_cycle_chart(request):
    colony = _colony()
    if colony is None:
        return JsonResponse({"labels": [], "datasets": []})
    recent = list(
        colony.cycles.filter(status=ColonyCycle.STATUS_COMPLETED)
        .order_by("-number")[:50]
    )[::-1]

    def series(picker):
        return [picker(c.stats or {}) or 0 for c in recent]

    return JsonResponse({
        "labels": [c.number for c in recent],
        "datasets": [
            {"label": "Pheromone mass", "data": series(lambda s: s.get("ph_mass"))},
            {"label": "Nodes visited", "data": series(
                lambda s: (s.get("castes", {}).get("forager", {}) or {}).get("nodes_visited")
            )},
            {"label": "Quarantined", "data": series(lambda s: s.get("quarantined"))},
            {"label": "Builds proposed", "data": series(
                lambda s: (s.get("castes", {}).get("architect", {}) or {}).get("builds_proposed")
            )},
            {"label": "Deletes proposed", "data": series(
                lambda s: (s.get("castes", {}).get("pruner", {}) or {}).get("deletes_proposed")
            )},
        ],
    })


@require_GET
@superuser_required
def api_pheromone_map(request):
    from toto.ravioli.connection import is_enabled

    if not is_enabled():
        return JsonResponse({"nodes": [], "edges": [], "error": "graph disabled"})
    limit = min(int(request.GET.get("limit", 150)), 500)
    try:
        with _graph_ops_readonly() as graph_ops:
            payload = graph_ops.hottest_subgraph(limit=limit)
    except Exception as exc:  # noqa: BLE001 — map degrades to empty
        return JsonResponse({"nodes": [], "edges": [], "error": str(exc)})
    return JsonResponse(payload)


@require_POST
@superuser_required
def api_proposal_op(request, pk, op_id):
    proposal = get_object_or_404(FormicaProposal, pk=pk)
    if proposal.status not in (
        FormicaProposal.STATUS_READY, FormicaProposal.STATUS_FAILED
    ):
        return JsonResponse({"error": f"Proposal is {proposal.status}."}, status=409)
    try:
        data = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)
    approval = data.get("approval")
    if approval not in ("pending", "approved", "rejected"):
        return JsonResponse({"error": "Invalid approval value."}, status=400)

    from .services import apply as apply_svc

    ops = proposal.op_list
    op = next((o for o in ops if o.get("op_id") == op_id), None)
    if op is None:
        return JsonResponse({"error": "Unknown op."}, status=404)
    if approval == "approved" and (op.get("validation") or {}).get("status") == "error":
        return JsonResponse({"error": "Cannot approve an invalid op."}, status=400)
    op["approval"] = approval
    proposal.ops = {"ops": ops}
    proposal.summary = apply_svc.summarize(ops)
    proposal.save(update_fields=["ops", "summary", "updated_at"])
    return JsonResponse({"op": op, "summary": proposal.summary})


@require_POST
@superuser_required
def api_proposal_apply(request, pk):
    from toto.ravioli.connection import is_enabled

    if not is_enabled():
        return JsonResponse(
            {"error": "RAVIOLI_ENABLED is False — cannot reach Neo4j."}, status=503
        )
    proposal = get_object_or_404(FormicaProposal, pk=pk)
    if proposal.status == FormicaProposal.STATUS_APPLIED:
        return JsonResponse({"error": "Proposal already applied."}, status=409)

    from .services import apply as apply_svc

    try:
        data = json.loads(request.body or "{}")
    except ValueError:
        data = {}
    if data.get("approve_all"):
        apply_svc.approve_all_valid(proposal)
    if data.get("reject_all"):
        ops = proposal.op_list
        for op in ops:
            op["approval"] = "rejected"
        proposal.ops = {"ops": ops}
        proposal.status = FormicaProposal.STATUS_REJECTED
        proposal.summary = apply_svc.summarize(ops)
        proposal.save()
        return JsonResponse({"status": proposal.status, "summary": proposal.summary})

    _result, errors = apply_svc.run(proposal.pk, user_id=request.user.pk)
    proposal.refresh_from_db()
    return JsonResponse({
        "status": proposal.status,
        "summary": proposal.summary,
        "errors": errors,
        "ops": proposal.op_list,
    })
