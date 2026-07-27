"""Connectors views — a Ravioli tab: configure sources → run ETL → audit runs.

The review surface itself is the ingestor's: ``run_review`` renders
``ingestor/ingestor.html`` booted straight into review phase, and every
edit/approval/apply goes through the existing pk-keyed ingestor endpoints.
"""

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Prefetch
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from toto.ui import PageProcessor

from .forms import DataConnectorForm
from .models import ConnectorRun, DataConnector
from .services import mapping as mapping_svc
from .services.extract import ExtractError, get_extractor
from .services.runner import RunInProgress, create_run, dispatch_run


def superuser_required(view_func):
    return user_passes_test(lambda u: u.is_active and u.is_superuser)(view_func)


def _run_payload(run):
    return {
        "run_id": run.pk,
        "status": run.status,
        "stats": run.stats,
        "error": run.error,
        "proposal_id": run.proposal_id,
        "detail_url": reverse("connectors:run_detail", args=[run.pk]),
        "review_url": reverse("connectors:run_review", args=[run.pk])
        if run.proposal_id
        else None,
    }


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------

@login_required
@superuser_required
def home(request):
    connectors = list(
        DataConnector.objects.select_related("http_connector").prefetch_related(
            Prefetch(
                "runs",
                queryset=ConnectorRun.objects.order_by("-created_at", "-id")[:1],
                to_attr="latest_runs",
            )
        )
    )
    for connector in connectors:
        connector.latest_run = connector.latest_runs[0] if connector.latest_runs else None
    recent_runs = ConnectorRun.objects.select_related("connector", "proposal")[:25]
    in_flight = any(
        run.status in (ConnectorRun.STATUS_PENDING, ConnectorRun.STATUS_RUNNING)
        for run in recent_runs
    )
    context = PageProcessor().decorate(
        {
            "connectors": connectors,
            "recent_runs": recent_runs,
            "in_flight": in_flight,
        },
        request,
    )
    return render(request, "connectors/connectors.html", context)


@login_required
@superuser_required
def connector_edit(request, pk=None):
    connector = get_object_or_404(DataConnector, pk=pk) if pk else None
    if request.method == "POST":
        form = DataConnectorForm(request.POST, instance=connector)
        if form.is_valid():
            connector = form.save(commit=False)
            connector.owner = connector.owner or request.user
            connector.save()
            messages.success(request, f"Connector '{connector.name}' saved.")
            return redirect("connectors:detail", pk=connector.pk)
    else:
        form = DataConnectorForm(instance=connector)
    runs = connector.runs.select_related("proposal")[:25] if connector else []
    context = PageProcessor().decorate(
        {"form": form, "connector": connector, "runs": runs},
        request,
    )
    return render(request, "connectors/connector_edit.html", context)


@login_required
@superuser_required
def run_detail(request, pk):
    run = get_object_or_404(
        ConnectorRun.objects.select_related(
            "connector", "proposal", "raw_payload_file"
        ),
        pk=pk,
    )
    # Belt-and-braces beside the post_save signal: a reviewer may have applied
    # the proposal in another tab.
    run.sync_from_proposal()
    context = PageProcessor().decorate(
        {
            "run": run,
            "in_flight": run.status
            in (ConnectorRun.STATUS_PENDING, ConnectorRun.STATUS_RUNNING),
        },
        request,
    )
    return render(request, "connectors/run_detail.html", context)


@login_required
@superuser_required
def run_review(request, pk):
    """Render the ingestor review surface booted into this run's proposal."""
    from toto.ingestor.views import bento_templates, proposal_payload

    run = get_object_or_404(
        ConnectorRun.objects.select_related("proposal", "connector"), pk=pk
    )
    if run.proposal is None:
        messages.warning(request, "This run produced no reviewable proposal.")
        return redirect("connectors:run_detail", pk=run.pk)
    run.sync_from_proposal()

    from toto.ravioli.connection import is_enabled

    categories, edge_types = ([], [])
    if is_enabled():
        try:
            categories, edge_types = bento_templates()
        except Exception:  # noqa: BLE001 — graph may be down; page still renders
            categories, edge_types = ([], [])

    context = PageProcessor().decorate(
        {
            "categories_json": json.dumps(categories),
            "edge_types_json": json.dumps(edge_types),
            "generate_url": reverse("ingestor:generate"),
            "prefill_text": "",
            "initial_payload": proposal_payload(run.proposal),
            "active_tab": "connectors",
        },
        request,
    )
    return render(request, "ingestor/ingestor.html", context)


# ---------------------------------------------------------------------------
# actions / JSON endpoints
# ---------------------------------------------------------------------------

@require_POST
@superuser_required
def run_now(request, pk):
    connector = get_object_or_404(DataConnector, pk=pk)
    if not connector.is_active:
        messages.error(request, f"Connector '{connector.name}' is inactive.")
        return redirect("connectors:home")
    try:
        run = create_run(
            connector, triggered=ConnectorRun.TRIGGERED_MANUAL, user=request.user
        )
    except RunInProgress as exc:
        messages.warning(request, str(exc))
        return redirect("connectors:home")
    queued = dispatch_run(run)
    messages.success(
        request,
        f"Run #{run.pk} {'queued' if queued else 'executed inline'} "
        f"for '{connector.name}'.",
    )
    return redirect("connectors:run_detail", pk=run.pk)


@require_POST
@superuser_required
def validate_config(request):
    """Validate submitted mapping_spec/extract_config without saving."""
    try:
        data = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"error": "Invalid JSON body."}, status=400)

    mapping_errors = []
    spec = data.get("mapping_spec")
    if isinstance(spec, str):
        try:
            spec = json.loads(spec or "{}")
        except ValueError as exc:
            mapping_errors.append(f"mapping_spec is not valid JSON: {exc}")
            spec = None
    if spec is not None and not mapping_errors:
        try:
            mapping_errors = mapping_svc.validate_mapping_spec(spec)
        except Exception:  # noqa: BLE001 — Bento/DB unavailable
            mapping_errors = mapping_svc.validate_mapping_spec(spec, check_bento=False)

    extract_errors = []
    config = data.get("extract_config")
    if isinstance(config, str):
        try:
            config = json.loads(config or "{}")
        except ValueError as exc:
            extract_errors.append(f"extract_config is not valid JSON: {exc}")
            config = None
    if config is not None and not extract_errors:
        try:
            extractor = get_extractor(data.get("extractor_kind") or "rest_api")
        except ExtractError as exc:
            extract_errors.append(str(exc))
        else:
            extract_errors = extractor.validate_config(config)

    return JsonResponse(
        {"mapping_errors": mapping_errors, "extract_errors": extract_errors}
    )


@require_GET
@superuser_required
def run_status(request, pk):
    run = get_object_or_404(ConnectorRun.objects.select_related("proposal"), pk=pk)
    run.sync_from_proposal()
    return JsonResponse(_run_payload(run))
