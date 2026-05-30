import json
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor
from toto.response.models import (
    Deployment,
    DeploymentRoute,
    EvacuationRoute,
)

from .models import (
    AchievementBadge,
    PersonAchievement,
    IncidentType,
    Responder,
    ResponderSkill,
    MobilizationReport,
    MobilizationEvent,
    EmergencyStatus,
    EmergencyEquipmentAccess,
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
    from toto.people.models import Person
    from toto.socialhub.models import Community

    q = request.GET.get("q", "")

    existing_responder_person_ids = Responder.objects.values_list("person_id", flat=True)

    federal_tribe_community_ids = Community.objects.filter(
        is_federal_tribe=True
    ).values_list("id", flat=True)

    from toto.people.civic import committed_citizen_filter
    eligible = Person.objects.filter(
        committed_citizen_filter() | Q(communities__in=federal_tribe_community_ids)
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
        ).prefetch_related("evidence_links__incident", "mobilization_events"),
        pk=pk,
    )
    person = _person(request)
    can_enact = person and services.can_enact_report(person, report)

    return _render(request, "mobilization/report_detail.html", {
        "report": report,
        "can_enact": can_enact,
        "evidence": report.evidence_links.select_related("incident", "added_by").all(),
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

    campaign = event.kanban_campaign
    campaign_missions = []
    if campaign:
        from toto.kanban.models import Mission
        campaign_missions = list(
            campaign.missions.select_related("location", "route", "owner").prefetch_related(
                "tasks__assignee__person"
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

    pending_emergency_proposals = AssemblyProposal.objects.filter(
        proposal_type=AssemblyProposalType.EMERGENCY_DECLARATION,
        metadata__event_id=event.pk,
    ).exclude(status__in=[AssemblyStatus.REJECTED, AssemblyStatus.EXPIRED]).order_by("-created_at")

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

    from django.urls import reverse
    return _render(request, "mobilization/event_detail.html", {
        "event": event,
        "deployments": deployments,
        "campaign": campaign,
        "campaign_missions": campaign_missions,
        "map_data_url": reverse("mobilization:event_map_data", args=[pk]),
        "layer_toggles": [
            ("deployments",     "Deployments",      "fa-solid fa-users-gear",          "accent"),
            ("evac_routes",     "Evac Routes",       "fa-solid fa-route",               "success"),
            ("emergency_zones", "Emergency Zones",   "fa-solid fa-triangle-exclamation","warn"),
            ("incidents",       "Incidents",         "fa-solid fa-circle-exclamation",  "caution"),
            ("campaign_zone",   "Campaign Zone",     "fa-solid fa-draw-polygon",        "accent"),
        ],
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
                "properties": {"kind": "campaign_zone", "label": campaign.name},
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
                done = sum(1 for iv in dep.interventions.all() if iv.status == "done")
                total = dep.interventions.count()
                features.append({
                    "type": "Feature",
                    "properties": {
                        "kind": "deployment",
                        "label": dep.title,
                        "status": dep.status,
                        "priority": dep.priority,
                        "type": dep.get_deployment_type_display(),
                        "interventions_done": done,
                        "interventions_total": total,
                        "is_hybrid": dep.is_hybrid,
                    },
                    "geometry": pt,
                })

    for er in event.evac_routes.select_related("route"):
        if not er.route_id:
            continue
        try:
            geom = json.loads(er.route.geometry.geojson) if er.route.geometry else None
        except Exception:
            geom = None
        if geom:
            features.append({
                "type": "Feature",
                "properties": {
                    "kind": "evac_route",
                    "label": er.name,
                    "route_type": er.route_type,
                    "status": er.status,
                },
                "geometry": geom,
            })

    for es in event.emergency_statuses.select_related("community__territory", "zone"):
        geom = None
        label = ""
        try:
            if es.zone and es.zone.geometry:
                geom = json.loads(es.zone.geometry.geojson)
                label = str(es.zone)
            elif es.community and hasattr(es.community, "territory") and es.community.territory and es.community.territory.geometry:
                geom = json.loads(es.community.territory.geometry.geojson)
                label = str(es.community)
        except Exception:
            geom = None
        if geom:
            features.append({
                "type": "Feature",
                "properties": {
                    "kind": "emergency_zone",
                    "label": label,
                    "level": es.level,
                    "status": es.status,
                },
                "geometry": geom,
            })

    from toto.incidents.models import Incident
    incidents = Incident.objects.filter(
        mobilization_evidence__report__mobilization_events=event
    ).distinct().select_related("address", "zone")
    for inc in incidents:
        pt = None
        try:
            geom = inc.map_geometry
            if geom:
                if geom.geom_type == "Point":
                    pt = json.loads(geom.geojson)
                else:
                    pt = json.loads(geom.centroid.geojson)
        except Exception:
            pt = None
        if pt:
            features.append({
                "type": "Feature",
                "properties": {
                    "kind": "incident",
                    "label": inc.title,
                    "severity": inc.severity,
                    "incident_type": inc.incident_type,
                    "status": inc.status,
                },
                "geometry": pt,
            })

    return JsonResponse({"type": "FeatureCollection", "features": features})


# ---------------------------------------------------------------------------
# Deployment creation (accessed from event detail page)
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
            return redirect("response:deployment_detail", pk=dep.pk)

    missions = []
    if event.kanban_campaign:
        missions = list(event.kanban_campaign.missions.order_by("title"))

    return _render(request, "mobilization/deployment_form.html", {
        "event": event,
        "missions": missions,
        "deployment_type_choices": Deployment.DEPLOYMENT_TYPE_CHOICES,
        "priority_choices": [("low", "Low"), ("normal", "Normal"), ("high", "High"), ("urgent", "Urgent")],
    })


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
# Emergency status — declaration must pass through assembly
# ---------------------------------------------------------------------------

@login_required
@require_POST
def emergency_status_propose(request, pk):
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
