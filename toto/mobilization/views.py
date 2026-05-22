import json
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone

from toto.ui import PageProcessor

from .models import (
    AchievementBadge,
    PersonAchievement,
    IncidentType,
    InterventionType,
    Responder,
    ResponderSkill,
    MobilizationReport,
    MobilizationEvent,
    Deployment,
    DeploymentAssignment,
    DeploymentEquipment,
    DeploymentRoute,
    EmergencyEquipmentAccess,
    EmergencyStatus,
    EvacuationRoute,
    Intervention,
)
from . import services


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
# Overview
# ---------------------------------------------------------------------------

@login_required
def overview(request):
    responder_count = Responder.objects.filter(is_active=True).count()
    available_count = Responder.objects.filter(current_status="available", is_active=True).count()
    responding_count = Responder.objects.filter(current_status="responding").count()

    report_counts = {
        s: MobilizationReport.objects.filter(status=s).count()
        for s in ("draft", "submitted", "reviewed", "enacted", "rejected")
    }

    active_events = MobilizationEvent.objects.filter(status="active").select_related(
        "community", "incident_type", "coordinator"
    ).order_by("-created_at")[:5]

    active_deployments = Deployment.objects.filter(status="active").select_related(
        "event", "community", "coordinator"
    ).order_by("-created_at")[:5]

    severity_data = {
        s: MobilizationReport.objects.filter(severity=s).count()
        for s in ("low", "medium", "high", "critical")
    }

    deployment_type_data = list(
        Deployment.objects.values("deployment_type")
        .annotate(count=Count("id"))
        .order_by("-count")[:8]
    )

    incident_type_data = list(
        MobilizationEvent.objects.values("incident_type__name")
        .annotate(count=Count("id"))
        .exclude(incident_type=None)
        .order_by("-count")[:8]
    )

    recent_reports = MobilizationReport.objects.select_related(
        "community", "incident_type", "submitted_by"
    ).order_by("-created_at")[:8]

    return _render(request, "mobilization/overview.html", {
        "responder_count": responder_count,
        "available_count": available_count,
        "responding_count": responding_count,
        "report_counts": report_counts,
        "active_events": active_events,
        "active_deployments": active_deployments,
        "severity_data": json.dumps(severity_data),
        "deployment_type_data": json.dumps(deployment_type_data),
        "incident_type_data": json.dumps(incident_type_data),
        "recent_reports": recent_reports,
    })


# ---------------------------------------------------------------------------
# Responders
# ---------------------------------------------------------------------------

@login_required
def responder_list(request):
    status_filter = request.GET.get("status", "")
    q = request.GET.get("q", "")

    qs = Responder.objects.select_related("person").prefetch_related("communities", "skills__skill")
    if status_filter:
        qs = qs.filter(current_status=status_filter)
    if q:
        qs = qs.filter(person__display_name__icontains=q)

    status_counts_list = [
        (val, label, Responder.objects.filter(current_status=val).count())
        for val, label in Responder.CURRENT_STATUS_CHOICES
    ]

    return _render(request, "mobilization/responder_list.html", {
        "responders": qs.order_by("current_status", "person__display_name"),
        "status_filter": status_filter,
        "q": q,
        "status_counts_list": status_counts_list,
        "status_choices": Responder.CURRENT_STATUS_CHOICES,
    })


@login_required
def responder_detail(request, pk):
    responder = get_object_or_404(
        Responder.objects.select_related("person").prefetch_related(
            "communities", "skills__skill", "skills__verified_by",
            "deployment_assignments__deployment__event",
        ),
        pk=pk,
    )
    active_assignments = responder.deployment_assignments.filter(
        status="active"
    ).select_related("deployment__event", "deployment__community")

    all_assignments = responder.deployment_assignments.select_related(
        "deployment__event", "deployment__community", "assigned_by"
    ).order_by("-deployment__created_at")

    achievements = responder.person.mobilization_achievements.select_related(
        "badge", "deployment", "awarded_by"
    ).order_by("-awarded_at")

    return _render(request, "mobilization/responder_detail.html", {
        "responder": responder,
        "active_assignments": active_assignments,
        "all_assignments": all_assignments,
        "achievements": achievements,
    })


# ---------------------------------------------------------------------------
# Responder recruitment (call-in menu)
# ---------------------------------------------------------------------------

@login_required
def responder_recruit(request):
    """
    Shows eligible persons (federal agents OR members of a federal tribe community)
    who do not yet have a Responder profile — the call-in roster.
    """
    from toto.people.models import Person
    from toto.socialhub.models import Community

    q = request.GET.get("q", "")

    existing_responder_person_ids = Responder.objects.values_list("person_id", flat=True)

    federal_tribe_community_ids = Community.objects.filter(
        is_federal_tribe=True
    ).values_list("id", flat=True)

    eligible = Person.objects.filter(
        Q(is_federal_agent=True) | Q(communities__in=federal_tribe_community_ids)
    ).exclude(
        id__in=existing_responder_person_ids
    ).distinct().prefetch_related("communities")

    if q:
        eligible = eligible.filter(display_name__icontains=q)

    return _render(request, "mobilization/responder_recruit.html", {
        "eligible": eligible.order_by("display_name"),
        "q": q,
    })


@login_required
@require_POST
def responder_callin(request):
    """Creates a Responder profile for an eligible person."""
    from toto.people.models import Person
    person_id = request.POST.get("person_id")
    person = get_object_or_404(Person, pk=person_id)

    if hasattr(person, "responder_profile"):
        messages.warning(request, f"{person} is already a responder.")
        return redirect("mobilization:responder_recruit")

    responder = Responder(person=person, is_active=True)
    try:
        responder.full_clean()
        responder.save()
        messages.success(request, f"{person} added as a responder.")
        return redirect("mobilization:responder_detail", pk=responder.pk)
    except ValidationError as e:
        messages.error(request, str(e.message if hasattr(e, "message") else e))
        return redirect("mobilization:responder_recruit")


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@login_required
def report_list(request):
    status_filter = request.GET.get("status", "")
    severity_filter = request.GET.get("severity", "")
    q = request.GET.get("q", "")

    qs = MobilizationReport.objects.select_related("community", "incident_type", "submitted_by")
    if status_filter:
        qs = qs.filter(status=status_filter)
    if severity_filter:
        qs = qs.filter(severity=severity_filter)
    if q:
        qs = qs.filter(title__icontains=q)

    status_counts = {
        s: MobilizationReport.objects.filter(status=s).count()
        for s in ("draft", "submitted", "reviewed", "enacted", "rejected", "closed")
    }

    return _render(request, "mobilization/report_list.html", {
        "reports": qs.order_by("-created_at"),
        "status_filter": status_filter,
        "severity_filter": severity_filter,
        "q": q,
        "status_counts": status_counts,
        "status_choices": MobilizationReport.STATUS_CHOICES,
        "severity_choices": [("low", "Low"), ("medium", "Medium"), ("high", "High"), ("critical", "Critical")],
    })


@login_required
def report_create(request):
    incident_types = IncidentType.objects.all()
    from toto.socialhub.models import Community
    communities = Community.objects.order_by("name")

    if request.method == "POST":
        community_id = request.POST.get("community")
        title = request.POST.get("title", "").strip()
        incident_type_id = request.POST.get("incident_type")
        severity = request.POST.get("severity", "low")
        summary = request.POST.get("summary", "")
        justification = request.POST.get("justification", "")

        if not community_id or not title:
            messages.error(request, "Community and title are required.")
        else:
            from toto.socialhub.models import Community
            community = get_object_or_404(Community, pk=community_id)
            incident_type = IncidentType.objects.filter(pk=incident_type_id).first() if incident_type_id else None
            person = _person(request)
            report = MobilizationReport.objects.create(
                community=community,
                title=title,
                incident_type=incident_type,
                severity=severity,
                summary=summary,
                justification=justification,
                submitted_by=person,
            )
            messages.success(request, f'Report "{report.title}" created.')
            return redirect("mobilization:report_detail", pk=report.pk)

    return _render(request, "mobilization/report_form.html", {
        "incident_types": incident_types,
        "communities": communities,
        "severity_choices": [("low", "Low"), ("medium", "Medium"), ("high", "High"), ("critical", "Critical")],
    })


@login_required
def report_detail(request, pk):
    report = get_object_or_404(
        MobilizationReport.objects.select_related(
            "community", "incident_type", "submitted_by", "reviewed_by", "enacted_by"
        ).prefetch_related("evidence_links__detection", "mobilization_events"),
        pk=pk,
    )
    person = _person(request)
    can_enact = person and services.can_enact_report(person, report)

    return _render(request, "mobilization/report_detail.html", {
        "report": report,
        "can_enact": can_enact,
        "evidence": report.evidence_links.select_related("detection", "added_by").all(),
        "linked_events": report.mobilization_events.select_related("community").all(),
    })


@login_required
@require_POST
def report_submit(request, pk):
    report = get_object_or_404(MobilizationReport, pk=pk)
    person = _person(request)
    try:
        services.submit_report(report, person)
        messages.success(request, "Report submitted for review.")
    except ValidationError as e:
        messages.error(request, str(e.message))
    return redirect("mobilization:report_detail", pk=pk)


@login_required
@require_POST
def report_review(request, pk):
    report = get_object_or_404(MobilizationReport, pk=pk)
    person = _person(request)
    try:
        services.review_report(report, person)
        messages.success(request, "Report marked as reviewed.")
    except ValidationError as e:
        messages.error(request, str(e.message))
    return redirect("mobilization:report_detail", pk=pk)


@login_required
@require_POST
def report_enact(request, pk):
    report = get_object_or_404(MobilizationReport, pk=pk)
    person = _person(request)
    create_event = request.POST.get("create_event") == "1"
    try:
        _, event = services.enact_report(report, person, create_event=create_event)
        messages.success(request, "Report enacted." + (" Event created." if event else ""))
        if event:
            return redirect("mobilization:event_detail", pk=event.pk)
    except ValidationError as e:
        messages.error(request, str(e.message))
    return redirect("mobilization:report_detail", pk=pk)


@login_required
@require_POST
def report_reject(request, pk):
    report = get_object_or_404(MobilizationReport, pk=pk)
    person = _person(request)
    notes = request.POST.get("notes", "")
    try:
        services.reject_report(report, person, notes=notes)
        messages.success(request, "Report rejected.")
    except ValidationError as e:
        messages.error(request, str(e.message))
    return redirect("mobilization:report_detail", pk=pk)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@login_required
def event_list(request):
    status_filter = request.GET.get("status", "")
    q = request.GET.get("q", "")

    qs = MobilizationEvent.objects.select_related(
        "community", "incident_type", "coordinator", "source_report", "kanban_campaign"
    )
    if status_filter:
        qs = qs.filter(status=status_filter)
    if q:
        qs = qs.filter(title__icontains=q)

    status_counts = {
        s: MobilizationEvent.objects.filter(status=s).count()
        for s in ("standby", "active", "resolved", "cancelled")
    }

    return _render(request, "mobilization/event_list.html", {
        "events": qs.order_by("-created_at"),
        "status_filter": status_filter,
        "q": q,
        "status_counts": status_counts,
        "status_choices": MobilizationEvent.STATUS_CHOICES,
    })


@login_required
def event_detail(request, pk):
    event = get_object_or_404(
        MobilizationEvent.objects.select_related(
            "community", "incident_type", "coordinator", "source_report",
            "scheduled_event", "kanban_campaign__zone",
        ),
        pk=pk,
    )
    deployments = event.deployments.select_related(
        "coordinator", "kanban_mission__location", "kanban_mission__route"
    ).prefetch_related("assignments__responder__person", "interventions").order_by("-created_at")

    # Kanban campaign data
    campaign = event.kanban_campaign
    campaign_missions = []
    if campaign:
        from toto.kanban.models import Mission, Task
        campaign_missions = list(
            campaign.missions.select_related("location", "route", "owner").prefetch_related(
                "tasks__assignee"
            ).order_by("-urgency", "-impact")
        )

    evac_routes = event.evac_routes.select_related("route").order_by("route_type", "name")
    emergency_statuses = event.emergency_statuses.select_related(
        "community", "zone", "declared_by", "source_proposal"
    ).prefetch_related("equipment_accesses__item", "equipment_accesses__deployment").order_by("-declared_at")
    from toto.locations.models import Route, Zone
    from toto.socialhub.models import Community
    from toto.assembly.models import AssemblyProposal, AssemblyProposalType, AssemblyStatus
    available_routes = Route.objects.order_by("name")
    all_communities = Community.objects.order_by("name")
    all_zones = Zone.objects.order_by("name")

    # Pending/passed emergency declaration proposals for this event
    pending_emergency_proposals = AssemblyProposal.objects.filter(
        proposal_type=AssemblyProposalType.EMERGENCY_DECLARATION,
        metadata__event_id=event.pk,
    ).exclude(status__in=[AssemblyStatus.REJECTED, AssemblyStatus.EXPIRED]).order_by("-created_at")

    # Build escalation / hierarchy data for Cytoscape
    escalation_nodes = []
    escalation_edges = []
    if event.coordinator:
        escalation_nodes.append({"data": {"id": f"coord_{event.coordinator_id}", "label": str(event.coordinator), "type": "coordinator"}})
    for dep in deployments:
        dep_id = f"dep_{dep.pk}"
        escalation_nodes.append({"data": {"id": dep_id, "label": dep.title, "type": "deployment", "status": dep.status}})
        if event.coordinator:
            escalation_edges.append({"data": {"source": f"coord_{event.coordinator_id}", "target": dep_id}})
        for a in dep.assignments.filter(role="lead").select_related("responder__person"):
            lead_id = f"lead_{a.pk}"
            escalation_nodes.append({"data": {"id": lead_id, "label": str(a.responder.person), "type": "lead"}})
            escalation_edges.append({"data": {"source": dep_id, "target": lead_id}})

    # Mission timeline data for Chart.js
    mission_timeline = []
    if campaign_missions:
        for m in campaign_missions:
            mission_timeline.append({
                "label": m.title,
                "urgency": m.urgency,
                "impact": m.impact,
                "task_count": m.tasks.count(),
                "done_count": m.tasks.filter(completed_at__isnull=False).count(),
            })

    return _render(request, "mobilization/event_detail.html", {
        "event": event,
        "deployments": deployments,
        "campaign": campaign,
        "campaign_missions": campaign_missions,
        "map_data_url": f"/mobilization/events/{pk}/map-data/",
        "evac_routes": evac_routes,
        "available_routes": available_routes,
        "evac_route_type_choices": EvacuationRoute.ROUTE_TYPE_CHOICES,
        "evac_status_choices": EvacuationRoute.STATUS_CHOICES,
        "emergency_statuses": emergency_statuses,
        "emergency_level_choices": EmergencyStatus.LEVEL_CHOICES,
        "all_communities": all_communities,
        "all_zones": all_zones,
        "pending_emergency_proposals": pending_emergency_proposals,
        "escalation_nodes": json.dumps(escalation_nodes),
        "escalation_edges": json.dumps(escalation_edges),
        "mission_timeline": json.dumps(mission_timeline),
    })


@login_required
def event_map_data(request, pk):
    event = get_object_or_404(
        MobilizationEvent.objects.select_related("kanban_campaign__zone"),
        pk=pk,
    )

    features = []

    campaign = event.kanban_campaign
    if campaign and campaign.zone:
        zone = campaign.zone
        try:
            geom = json.loads(zone.geometry.geojson) if zone.geometry else None
        except Exception:
            geom = None
        if geom:
            features.append({
                "type": "Feature",
                "properties": {
                    "kind": "campaign_zone",
                    "label": campaign.name,
                    "color": "#3b82f6",
                },
                "geometry": geom,
            })

    deployments = event.deployments.select_related(
        "kanban_mission__location", "kanban_mission__route"
    ).prefetch_related("interventions")

    for dep in deployments:
        if dep.kanban_mission and dep.kanban_mission.location:
            addr = dep.kanban_mission.location
            try:
                pt = json.loads(addr.geometry.geojson) if addr.geometry else None
            except Exception:
                pt = None
            if pt:
                features.append({
                    "type": "Feature",
                    "properties": {
                        "kind": "mission",
                        "label": dep.kanban_mission.title,
                        "deployment": dep.title,
                        "urgency": dep.kanban_mission.urgency,
                        "impact": dep.kanban_mission.impact,
                        "status": dep.status,
                        "color": _deployment_color(dep.status),
                    },
                    "geometry": pt,
                })

    return JsonResponse({"type": "FeatureCollection", "features": features})


def _deployment_color(status):
    return {
        "planned": "#94a3b8",
        "active": "#22c55e",
        "paused": "#f59e0b",
        "completed": "#6366f1",
        "cancelled": "#ef4444",
    }.get(status, "#94a3b8")


# ---------------------------------------------------------------------------
# Deployments
# ---------------------------------------------------------------------------

@login_required
def deployment_create(request, pk):
    event = get_object_or_404(MobilizationEvent, pk=pk)
    from toto.kanban.models import Mission

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        deployment_type = request.POST.get("deployment_type", "other")
        priority = request.POST.get("priority", "normal")
        objective = request.POST.get("objective", "")
        mission_id = request.POST.get("kanban_mission")
        mission = Mission.objects.filter(pk=mission_id).first() if mission_id else None
        person = _person(request)

        if not title:
            messages.error(request, "Title is required.")
        else:
            dep = services.create_deployment(
                event, event.community,
                title=title,
                deployment_type=deployment_type,
                priority=priority,
                objective=objective,
                coordinator=person,
                kanban_mission=mission,
            )
            messages.success(request, f'Deployment "{dep.title}" created.')
            return redirect("mobilization:deployment_detail", pk=dep.pk)

    missions = []
    if event.kanban_campaign:
        missions = list(event.kanban_campaign.missions.order_by("title"))

    return _render(request, "mobilization/deployment_form.html", {
        "event": event,
        "missions": missions,
        "deployment_type_choices": Deployment.DEPLOYMENT_TYPE_CHOICES,
        "priority_choices": [("low", "Low"), ("normal", "Normal"), ("high", "High"), ("urgent", "Urgent")],
    })


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

    return _render(request, "mobilization/deployment_list.html", {
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

    # Kanban mission data
    mission = deployment.kanban_mission
    mission_tasks = []
    if mission:
        from toto.kanban.models import Task
        mission_tasks = list(
            mission.tasks.select_related("assignee", "column").order_by("column__position", "position")
        )

    equipment = deployment.equipment.select_related("item__location", "item__object_type").order_by("item__name")
    dep_routes = deployment.routes.select_related("route").order_by("route_type")
    from toto.locations.models import Route
    from toto.inventory.models import RealWorldObject
    from toto.detections.models import Detection
    available_routes = Route.objects.order_by("name")
    available_items = RealWorldObject.objects.select_related("location", "object_type").order_by("name")

    # Detections from the event's source report evidence
    event = deployment.event
    report_detections = Detection.objects.filter(
        mobilization_evidence__report__mobilization_events=event
    ).distinct().select_related("category").order_by("-start_time")

    # Detections already linked to this deployment's interventions
    linked_detection_ids = set(
        interventions.exclude(detection=None).values_list("detection_id", flat=True)
    )

    return _render(request, "mobilization/deployment_detail.html", {
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
    })


@login_required
@require_POST
def assignment_create(request, pk):
    deployment = get_object_or_404(Deployment, pk=pk)
    responder_id = request.POST.get("responder")
    role = request.POST.get("role", "responder")
    person = _person(request)

    if not responder_id:
        messages.error(request, "Select a responder.")
        return redirect("mobilization:deployment_detail", pk=pk)

    responder = get_object_or_404(Responder, pk=responder_id)
    try:
        services.assign_responder_to_deployment(deployment, responder, assigned_by=person, role=role)
        messages.success(request, f"{responder.person} assigned to deployment.")
    except ValidationError as e:
        messages.error(request, str(e.message))
    return redirect("mobilization:deployment_detail", pk=pk)


@login_required
@require_POST
def assignment_activate(request, pk, assignment_pk):
    assignment = get_object_or_404(DeploymentAssignment, pk=assignment_pk, deployment_id=pk)
    try:
        services.activate_deployment_assignment(assignment)
        messages.success(request, "Assignment activated — responder is now responding.")
    except Exception as e:
        messages.error(request, str(e))
    return redirect("mobilization:deployment_detail", pk=pk)


@login_required
@require_POST
def assignment_release(request, pk, assignment_pk):
    assignment = get_object_or_404(DeploymentAssignment, pk=assignment_pk, deployment_id=pk)
    try:
        services.release_responder_from_deployment(assignment)
        messages.success(request, "Responder released.")
    except Exception as e:
        messages.error(request, str(e))
    return redirect("mobilization:deployment_detail", pk=pk)


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
    return redirect("mobilization:deployment_detail", pk=pk)


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

    # Detections linked to this event's community/report evidence, or the event's area
    event = deployment.event
    event_detections = Detection.objects.filter(
        mobilization_evidence__report__mobilization_events=event
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
            return redirect("mobilization:deployment_detail", pk=pk)

    return _render(request, "mobilization/intervention_form.html", {
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
    return redirect("mobilization:deployment_detail", pk=intervention.deployment_id)


@login_required
@require_POST
def intervention_review(request, pk):
    intervention = get_object_or_404(Intervention, pk=pk)
    person = _person(request)
    effect_description = request.POST.get("effect_description", "")
    reward_amount = request.POST.get("reward_amount") or None
    reward_asset_id = request.POST.get("reward_asset") or None

    if reward_amount:
        try:
            reward_amount = float(reward_amount)
        except ValueError:
            reward_amount = None

    from toto.assets.models import Asset
    reward_asset = Asset.objects.filter(pk=reward_asset_id).first() if reward_asset_id else None

    intervention.reviewer = person
    intervention.reviewed_at = timezone.now()
    if effect_description:
        intervention.effect_description = effect_description
    if reward_amount is not None:
        intervention.reward_amount = reward_amount
    if reward_asset:
        intervention.reward_asset = reward_asset
    intervention.save()
    messages.success(request, "Intervention reviewed.")
    return redirect("mobilization:deployment_detail", pk=intervention.deployment_id)


# ---------------------------------------------------------------------------
# Evacuation routes
# ---------------------------------------------------------------------------

@login_required
@require_POST
def evac_route_add(request, pk):
    event = get_object_or_404(MobilizationEvent, pk=pk)
    from toto.locations.models import Route
    route_id = request.POST.get("route")
    name = request.POST.get("name", "").strip()
    route_type = request.POST.get("route_type", "evacuation")
    notes = request.POST.get("notes", "")

    route = Route.objects.filter(pk=route_id).first() if route_id else None
    if not route or not name:
        messages.error(request, "Route and name are required.")
    else:
        EvacuationRoute.objects.create(
            event=event, route=route, name=name,
            route_type=route_type, notes=notes,
        )
        messages.success(request, f'Evacuation route "{name}" added.')
    return redirect("mobilization:event_detail", pk=pk)


@login_required
@require_POST
def evac_route_status(request, pk, route_pk):
    evac_route = get_object_or_404(EvacuationRoute, pk=route_pk, event_id=pk)
    status = request.POST.get("status", "active")
    evac_route.status = status
    evac_route.save()
    messages.success(request, f"Route status updated to {status}.")
    return redirect("mobilization:event_detail", pk=pk)


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
    return redirect("mobilization:deployment_detail", pk=pk)


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
    return redirect("mobilization:deployment_detail", pk=pk)


# ---------------------------------------------------------------------------
# Emergency status — must go through assembly proposal
# ---------------------------------------------------------------------------

@login_required
@require_POST
def emergency_status_propose(request, pk):
    """
    Creates an AssemblyProposal of type 'emg_declare' for this event.
    The actual EmergencyStatus is only created once the proposal passes and
    a coordinator clicks 'Activate' (emergency_proposal_activate view).
    """
    event = get_object_or_404(MobilizationEvent, pk=pk)
    from toto.socialhub.models import Community
    from toto.locations.models import Zone
    from toto.assembly.models import AssemblyProposal, AssemblyProposalType, AssemblyStatus

    community_id = request.POST.get("community") or None
    zone_id = request.POST.get("zone") or None
    level = request.POST.get("level", "warning")
    allows_asset = request.POST.get("allows_asset_requisition") == "1"
    allows_inventory = request.POST.get("allows_inventory_access") == "1"
    allows_routes = request.POST.get("allows_route_commandeering") == "1"
    tax_rate = request.POST.get("emergency_tax_rate") or None
    notes = request.POST.get("notes", "")

    community = Community.objects.filter(pk=community_id).first() if community_id else None
    zone = Zone.objects.filter(pk=zone_id).first() if zone_id else None
    person = _person(request)

    if not community and not zone:
        messages.error(request, "Select a community or zone to propose emergency for.")
        return redirect("mobilization:event_detail", pk=pk)

    target = str(community or zone)
    proposal_community = community or (zone.community if hasattr(zone, "community") and zone.community_id else event.community)

    AssemblyProposal.objects.create(
        community=proposal_community,
        proposal_type=AssemblyProposalType.EMERGENCY_DECLARATION,
        status=AssemblyStatus.OPEN,
        title=f"Emergency Declaration — {target} ({event.title})",
        body=notes,
        opened_by=person,
        metadata={
            "event_id": event.pk,
            "community_id": community.pk if community else None,
            "zone_id": zone.pk if zone else None,
            "level": level,
            "allows_asset_requisition": allows_asset,
            "allows_inventory_access": allows_inventory,
            "allows_route_commandeering": allows_routes,
            "emergency_tax_rate": tax_rate,
        },
    )
    messages.success(request, f"Emergency declaration proposal submitted for {target}. Assembly must vote to approve.")
    return redirect("mobilization:event_detail", pk=pk)


@login_required
@require_POST
def emergency_proposal_activate(request, pk, proposal_pk):
    """
    Activates (enacts) a passed emergency declaration proposal, creating the EmergencyStatus.
    Only allowed if the proposal has status=passed.
    """
    event = get_object_or_404(MobilizationEvent, pk=pk)
    from toto.assembly.models import AssemblyProposal, AssemblyStatus
    from toto.socialhub.models import Community
    from toto.locations.models import Zone

    proposal = get_object_or_404(AssemblyProposal, pk=proposal_pk)
    if proposal.status != AssemblyStatus.PASSED:
        messages.error(request, "Proposal has not passed assembly vote.")
        return redirect("mobilization:event_detail", pk=pk)

    if EmergencyStatus.objects.filter(source_proposal=proposal).exists():
        messages.warning(request, "Emergency already activated for this proposal.")
        return redirect("mobilization:event_detail", pk=pk)

    meta = proposal.metadata or {}
    community = Community.objects.filter(pk=meta.get("community_id")).first()
    zone = Zone.objects.filter(pk=meta.get("zone_id")).first()
    person = _person(request)

    try:
        tax_rate_val = float(meta["emergency_tax_rate"]) if meta.get("emergency_tax_rate") else None
    except (ValueError, TypeError):
        tax_rate_val = None

    EmergencyStatus.objects.create(
        event=event,
        community=community,
        zone=zone,
        level=meta.get("level", "warning"),
        declared_by=person,
        notes=proposal.body,
        allows_asset_requisition=meta.get("allows_asset_requisition", True),
        allows_inventory_access=meta.get("allows_inventory_access", True),
        allows_route_commandeering=meta.get("allows_route_commandeering", False),
        emergency_tax_rate=tax_rate_val,
        source_proposal=proposal,
    )
    target = str(community or zone or "—")
    messages.success(request, f"Emergency status activated for {target}.")
    return redirect("mobilization:event_detail", pk=pk)


@login_required
@require_POST
def emergency_status_lift(request, pk, es_pk):
    es = get_object_or_404(EmergencyStatus, pk=es_pk, event_id=pk)
    person = _person(request)
    es.lift(lifted_by=person)
    messages.success(request, "Emergency status lifted.")
    return redirect("mobilization:event_detail", pk=pk)


@login_required
@require_POST
def emergency_equipment_authorize(request, pk, es_pk):
    es = get_object_or_404(EmergencyStatus, pk=es_pk, event_id=pk)
    from toto.inventory.models import RealWorldObject

    item_id = request.POST.get("item")
    deployment_id = request.POST.get("deployment") or None
    quantity = request.POST.get("quantity", "1")

    item = RealWorldObject.objects.filter(pk=item_id).first() if item_id else None
    deployment = Deployment.objects.filter(pk=deployment_id).first() if deployment_id else None

    try:
        quantity = float(quantity)
    except (ValueError, TypeError):
        quantity = 1

    if not item:
        messages.error(request, "Item is required.")
    else:
        person = _person(request)
        EmergencyEquipmentAccess.objects.get_or_create(
            emergency=es,
            item=item,
            defaults={
                "deployment": deployment,
                "is_hybrid": True,
                "quantity": quantity,
                "authorized_by": person,
            },
        )
        messages.success(request, f"{item.name} authorized as hybrid equipment.")
    return redirect("mobilization:event_detail", pk=pk)
