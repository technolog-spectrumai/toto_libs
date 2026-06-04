from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
import json

from toto.ui import PageProcessor
from toto.mobilization.models import Responder
from toto.mobilization import services

from .models import (
    Deployment,
    DeploymentAssignment,
    DeploymentEquipment,
    DeploymentRoute,
    EvacuationRoute,
    Intervention,
    InterventionType,
)


def _render(request, template, context):
    return __import__("django.shortcuts", fromlist=["render"]).render(
        request, template, PageProcessor().decorate(context, request)
    )


def _person(request):
    try:
        return request.user.person
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Deployments
# ---------------------------------------------------------------------------

@login_required
def deployment_list(request):
    status_filter = request.GET.get("status", "")
    q = request.GET.get("q", "")

    qs = Deployment.objects.select_related("event__community", "community", "coordinator", "kanban_mission")
    if status_filter:
        qs = qs.filter(status=status_filter)
    if q:
        qs = qs.filter(title__icontains=q)

    status_counts = {
        s: Deployment.objects.filter(status=s).count()
        for s in ("planned", "active", "paused", "completed", "cancelled")
    }

    return _render(request, "response/deployment_list.html", {
        "deployments": qs.order_by("-created_at"),
        "status_filter": status_filter,
        "q": q,
        "status_counts": status_counts,
        "status_choices": Deployment.STATUS_CHOICES,
    })


@login_required
def deployment_detail(request, pk):
    deployment = get_object_or_404(
        Deployment.objects.select_related(
            "event__community", "event__incident_type", "community",
            "coordinator", "kanban_mission__campaign",
        ),
        pk=pk,
    )
    assignments = deployment.assignments.select_related(
        "responder__person", "assigned_by"
    ).order_by("status", "responder__person__display_name")

    interventions = deployment.interventions.select_related(
        "assigned_to__person", "reported_by", "kanban_task", "detection", "intervention_type"
    ).order_by("priority", "status")

    available_responders = Responder.objects.filter(
        is_active=True, current_status__in=("available", "standby"),
        communities=deployment.community,
    ).select_related("person").exclude(
        pk__in=deployment.assignments.values_list("responder_id", flat=True)
    )

    mission = deployment.kanban_mission
    mission_tasks = []
    if mission:
        from toto.kanban.models import Task
        mission_tasks = list(
            mission.tasks.select_related("assignee__person", "column").order_by("column__position", "position")
        )

    equipment = deployment.equipment.select_related("item__location", "item__object_type").order_by("item__name")
    dep_routes = deployment.routes.select_related("route").order_by("route_type")
    from toto.locations.models import Route
    from toto.inventory.models import RealWorldObject
    from toto.detections.models import Detection
    available_routes = Route.objects.order_by("name")
    available_items = RealWorldObject.objects.select_related("location", "object_type").order_by("name")

    event = deployment.event
    report_detections = Detection.objects.filter(
        promoted_incident__mobilization_evidence__report__mobilization_events=event
    ).distinct().select_related("category").order_by("-start_time")

    linked_detection_ids = set(
        interventions.exclude(detection=None).values_list("detection_id", flat=True)
    )

    from django.urls import reverse
    map_data_url = reverse("response:deployment_map_data", args=[pk])

    from toto.contracts.models import ContractNode
    person_ids = [a.responder.person_id for a in assignments]
    payroll_by_person = {}
    if person_ids:
        nodes = (
            ContractNode.objects
            .filter(
                key="worker_person",
                object_app="people",
                object_model="person",
                object_id__in=[str(pid) for pid in person_ids],
                contract__metadata__archetype="payroll",
            )
            .select_related("contract")
        )
        for node in nodes:
            payroll_by_person.setdefault(int(node.object_id), []).append(node.contract)

    assignment_payroll = [
        {"assignment": a, "payroll_contracts": payroll_by_person.get(a.responder.person_id, [])}
        for a in assignments
    ]

    # Deployment budget: contracts linked to this deployment
    from toto.contracts.models import Contract
    deployment_budget_nodes = (
        ContractNode.objects
        .filter(
            object_app="response",
            object_model="deployment",
            object_id=str(pk),
        )
        .select_related("contract")
    )
    deployment_contracts = [n.contract for n in deployment_budget_nodes]

    # All contracts for the link form
    linked_contract_ids = {c.pk for c in deployment_contracts}
    all_contracts_for_link = Contract.objects.exclude(pk__in=linked_contract_ids).order_by("name")

    return _render(request, "response/deployment_detail.html", {
        "deployment": deployment,
        "assignments": assignments,
        "interventions": interventions,
        "available_responders": available_responders,
        "mission": mission,
        "mission_tasks": mission_tasks,
        "equipment": equipment,
        "dep_routes": dep_routes,
        "available_routes": available_routes,
        "available_items": available_items,
        "route_type_choices": DeploymentRoute.ROUTE_TYPE_CHOICES,
        "report_detections": report_detections,
        "linked_detection_ids": linked_detection_ids,
        "map_data_url": map_data_url,
        "assignment_payroll": assignment_payroll,
        "deployment_contracts": deployment_contracts,
        "all_contracts_for_link": all_contracts_for_link,
        "dep_layer_toggles": [
            ("mission",     "Mission",    "fa-solid fa-crosshairs",          "accent"),
            ("dep_routes",  "Routes",     "fa-solid fa-route",               "success"),
            ("equipment",   "Equipment",  "fa-solid fa-toolbox",             "caution"),
            ("detections",  "Detections", "fa-solid fa-circle-exclamation",  "warn"),
        ],
    })


@login_required
def deployment_map_data(request, pk):
    deployment = get_object_or_404(
        Deployment.objects.select_related("kanban_mission__location"),
        pk=pk,
    )

    features = []

    if deployment.kanban_mission and deployment.kanban_mission.location:
        addr = deployment.kanban_mission.location
        try:
            pt = json.loads(addr.geometry.geojson) if addr.geometry else None
        except Exception:
            pt = None
        if pt:
            features.append({
                "type": "Feature",
                "properties": {
                    "kind": "mission",
                    "label": deployment.kanban_mission.title,
                    "status": deployment.status,
                },
                "geometry": pt,
            })

    for dr in deployment.routes.select_related("route"):
        try:
            geom = json.loads(dr.route.geometry.geojson) if dr.route.geometry else None
        except Exception:
            geom = None
        if geom:
            features.append({
                "type": "Feature",
                "properties": {
                    "kind": "dep_route",
                    "label": dr.route.name,
                    "route_type": dr.route_type,
                },
                "geometry": geom,
            })

    for eq in deployment.equipment.select_related("item__location"):
        item = eq.item
        if not item.location_id:
            continue
        try:
            pt = json.loads(item.location.geometry.geojson) if item.location.geometry else None
        except Exception:
            pt = None
        if pt:
            features.append({
                "type": "Feature",
                "properties": {
                    "kind": "equipment",
                    "label": item.name,
                    "quantity": float(eq.quantity),
                },
                "geometry": pt,
            })

    for iv in deployment.interventions.select_related("detection__address", "detection__zone"):
        if not iv.detection_id:
            continue
        det = iv.detection
        pt = None
        try:
            if det.address and det.address.geometry:
                pt = json.loads(det.address.geometry.geojson)
            elif det.zone and det.zone.geometry:
                pt = json.loads(det.zone.geometry.centroid.geojson)
        except Exception:
            pt = None
        if pt:
            features.append({
                "type": "Feature",
                "properties": {
                    "kind": "detection",
                    "label": det.title,
                    "severity": det.severity,
                    "intervention": iv.title,
                    "status": det.status,
                },
                "geometry": pt,
            })

    return JsonResponse({"type": "FeatureCollection", "features": features})


@login_required
@require_POST
def assignment_create(request, pk):
    deployment = get_object_or_404(Deployment, pk=pk)
    responder_id = request.POST.get("responder")
    role = request.POST.get("role", "responder")
    person = _person(request)

    if not responder_id:
        messages.error(request, "Select a responder.")
        return redirect("response:deployment_detail", pk=pk)

    responder = get_object_or_404(Responder, pk=responder_id)
    try:
        services.assign_responder_to_deployment(deployment, responder, assigned_by=person, role=role)
        messages.success(request, f"{responder.person} assigned to deployment.")
    except ValidationError as e:
        messages.error(request, str(e.message))
    return redirect("response:deployment_detail", pk=pk)


@login_required
@require_POST
def assignment_activate(request, pk, assignment_pk):
    assignment = get_object_or_404(DeploymentAssignment, pk=assignment_pk, deployment_id=pk)
    try:
        services.activate_deployment_assignment(assignment)
        messages.success(request, "Assignment activated — responder is now responding.")
    except Exception as e:
        messages.error(request, str(e))
    return redirect("response:deployment_detail", pk=pk)


@login_required
@require_POST
def assignment_release(request, pk, assignment_pk):
    assignment = get_object_or_404(DeploymentAssignment, pk=assignment_pk, deployment_id=pk)
    try:
        services.release_responder_from_deployment(assignment)
        messages.success(request, "Responder released.")
    except Exception as e:
        messages.error(request, str(e))
    return redirect("response:deployment_detail", pk=pk)


@login_required
@require_POST
def deployment_complete(request, pk):
    deployment = get_object_or_404(Deployment, pk=pk)
    force = request.POST.get("force") == "1"
    try:
        services.complete_deployment(deployment, force_complete=force)
        messages.success(request, "Deployment completed.")
    except ValidationError as e:
        messages.error(request, str(e.message))
    return redirect("response:deployment_detail", pk=pk)


@login_required
def intervention_create(request, pk):
    deployment = get_object_or_404(Deployment, pk=pk)
    from toto.kanban.models import Task
    from toto.detections.models import Detection
    mission_tasks = []
    if deployment.kanban_mission:
        mission_tasks = list(deployment.kanban_mission.tasks.select_related("column").order_by("position"))

    available_responders = deployment.assignments.filter(
        status__in=("assigned", "confirmed", "active")
    ).select_related("responder__person")

    intervention_types = InterventionType.objects.order_by("order", "name")

    event = deployment.event
    event_detections = Detection.objects.filter(
        promoted_incident__mobilization_evidence__report__mobilization_events=event
    ).distinct().select_related("category").order_by("-start_time")[:30]

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        intervention_type_id = request.POST.get("intervention_type") or None
        priority = request.POST.get("priority", "normal")
        description = request.POST.get("description", "")
        is_required = request.POST.get("is_required") == "1"
        task_id = request.POST.get("kanban_task")
        responder_id = request.POST.get("assigned_to")
        detection_id = request.POST.get("detection") or None
        task = Task.objects.filter(pk=task_id).first() if task_id else None
        responder = Responder.objects.filter(pk=responder_id).first() if responder_id else None
        intervention_type = InterventionType.objects.filter(pk=intervention_type_id).first() if intervention_type_id else None
        detection = Detection.objects.filter(pk=detection_id).first() if detection_id else None
        person = _person(request)

        if not title:
            messages.error(request, "Title is required.")
        else:
            iv = services.create_intervention(
                deployment,
                title=title,
                intervention_type=intervention_type,
                priority=priority,
                description=description,
                is_required=is_required,
                kanban_task=task,
                assigned_to=responder,
                reported_by=person,
                detection=detection,
            )
            messages.success(request, f'Intervention "{iv.title}" created.')
            return redirect("response:deployment_detail", pk=pk)

    return _render(request, "response/intervention_form.html", {
        "deployment": deployment,
        "mission_tasks": mission_tasks,
        "available_responders": available_responders,
        "intervention_types": intervention_types,
        "event_detections": event_detections,
        "priority_choices": [("low", "Low"), ("normal", "Normal"), ("high", "High"), ("urgent", "Urgent")],
    })


@login_required
@require_POST
def intervention_complete(request, pk):
    intervention = get_object_or_404(Intervention, pk=pk)
    outcome_notes = request.POST.get("outcome_notes", "")
    try:
        services.complete_intervention(intervention, outcome_notes=outcome_notes)
        messages.success(request, "Intervention marked done.")
    except Exception as e:
        messages.error(request, str(e))
    return redirect("response:deployment_detail", pk=intervention.deployment_id)


@login_required
@require_POST
def intervention_review(request, pk):
    intervention = get_object_or_404(Intervention, pk=pk)
    person = _person(request)
    effect_description = request.POST.get("effect_description", "")

    intervention.reviewer = person
    intervention.reviewed_at = timezone.now()
    if effect_description:
        intervention.effect_description = effect_description
    intervention.save()
    messages.success(request, "Intervention reviewed.")
    return redirect("response:deployment_detail", pk=intervention.deployment_id)


# ---------------------------------------------------------------------------
# Deployment routes
# ---------------------------------------------------------------------------

@login_required
@require_POST
def deployment_route_add(request, pk):
    deployment = get_object_or_404(Deployment, pk=pk)
    from toto.locations.models import Route
    route_id = request.POST.get("route")
    route_type = request.POST.get("route_type", "primary")
    notes = request.POST.get("notes", "")

    route = Route.objects.filter(pk=route_id).first() if route_id else None
    if not route:
        messages.error(request, "Route is required.")
    else:
        DeploymentRoute.objects.get_or_create(
            deployment=deployment, route=route,
            defaults={"route_type": route_type, "notes": notes},
        )
        messages.success(request, "Route added to deployment.")
    return redirect("response:deployment_detail", pk=pk)


# ---------------------------------------------------------------------------
# Deployment equipment
# ---------------------------------------------------------------------------

@login_required
@require_POST
def deployment_equipment_add(request, pk):
    deployment = get_object_or_404(Deployment, pk=pk)
    from toto.inventory.models import RealWorldObject
    item_id = request.POST.get("item")
    quantity = request.POST.get("quantity", "1")
    notes = request.POST.get("notes", "")

    item = RealWorldObject.objects.filter(pk=item_id).first() if item_id else None
    try:
        quantity = float(quantity) if quantity else 1
    except ValueError:
        quantity = 1

    if not item:
        messages.error(request, "Item is required.")
    else:
        DeploymentEquipment.objects.get_or_create(
            deployment=deployment, item=item,
            defaults={"quantity": quantity, "notes": notes},
        )
        messages.success(request, f"{item.name} added to deployment equipment.")
    return redirect("response:deployment_detail", pk=pk)


# ---------------------------------------------------------------------------
# Deployment budget (contract linking)
# ---------------------------------------------------------------------------

@login_required
@require_POST
def deployment_budget_link(request, pk):
    from toto.contracts.models import Contract, ContractNode
    deployment = get_object_or_404(Deployment, pk=pk)
    action = request.POST.get("action")

    if action == "link":
        contract_id = request.POST.get("contract_id")
        if contract_id:
            contract = get_object_or_404(Contract, pk=contract_id)
            ContractNode.objects.get_or_create(
                contract=contract,
                key="linked_deployment",
                object_app="response",
                object_model="deployment",
                object_id=str(pk),
                defaults={
                    "node_type": "manual",
                    "title": f"Deployment: {deployment.title}",
                    "is_manual": True,
                },
            )
            messages.success(request, f'Contract "{contract.name}" linked to deployment.')
    elif action == "unlink":
        contract_id = request.POST.get("contract_id")
        ContractNode.objects.filter(
            contract_id=contract_id,
            object_app="response",
            object_model="deployment",
            object_id=str(pk),
        ).delete()
        messages.success(request, "Contract unlinked from deployment.")

    return redirect("response:deployment_detail", pk=pk)


# ---------------------------------------------------------------------------
# Deployment metrics
# ---------------------------------------------------------------------------

@login_required
def deployment_metrics(request, pk):
    deployment = get_object_or_404(
        Deployment.objects.select_related("event__community", "community", "coordinator"),
        pk=pk,
    )

    assignments = deployment.assignments.select_related("responder__person")
    interventions = deployment.interventions.all()

    assignment_by_status = dict(
        assignments.values_list("status").annotate(n=Count("pk")).order_by()
    )
    assignment_by_role = dict(
        assignments.values_list("role").annotate(n=Count("pk")).order_by()
    )

    intervention_by_status = dict(
        interventions.values_list("status").annotate(n=Count("pk")).order_by()
    )
    intervention_by_priority = dict(
        interventions.values_list("priority").annotate(n=Count("pk")).order_by()
    )

    total_iv = interventions.count()
    done_iv = intervention_by_status.get("done", 0)
    completion_rate = round(done_iv / total_iv * 100) if total_iv else 0

    equipment_count = deployment.equipment.count()
    route_count = deployment.routes.count()

    return _render(request, "response/deployment_metrics.html", {
        "deployment": deployment,
        "assignments": assignments,
        "assignment_by_status": assignment_by_status,
        "assignment_by_role": assignment_by_role,
        "intervention_by_status": intervention_by_status,
        "intervention_by_priority": intervention_by_priority,
        "total_iv": total_iv,
        "done_iv": done_iv,
        "completion_rate": completion_rate,
        "equipment_count": equipment_count,
        "route_count": route_count,
    })

