from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor

from .forms import (
    ChargingDockForm,
    FleetForm,
    MaintenanceTicketForm,
    MissionWaypointForm,
    RobotAssignmentForm,
    RobotCommandForm,
    RobotForm,
    RobotMissionForm,
    RobotModelForm,
    RobotTelemetryForm,
)
from .models import (
    AssignmentStatus,
    ChargingDock,
    Fleet,
    MaintenanceStatus,
    MaintenanceTicket,
    MissionStatus,
    MissionWaypoint,
    Robot,
    RobotAssignment,
    RobotCommand,
    RobotEvent,
    RobotMission,
    RobotModel,
    RobotStatus,
    RobotTelemetry,
)
from .queries import (
    battery_distribution_chart_data,
    events_by_severity_chart_data,
    mission_status_chart_data,
    missions_over_time_chart_data,
    overview_stats,
    robot_status_chart_data,
)
from .selectors import robots_map_payload
from .services import (
    assign_robot,
    commission_robot,
    dispatch_mission,
    issue_command,
    record_robot_event,
    retire_robot,
)


def _render(request, template: str, context: dict):
    return render(request, template, PageProcessor().decorate(context, request))


def _robot_track_json(robot) -> dict:
    qs = (
        robot.telemetry
        .filter(latitude__isnull=False, longitude__isnull=False)
        .order_by("recorded_at")
        .values("recorded_at", "latitude", "longitude", "battery_percent", "speed_mps")[:200]
    )
    return {
        "track": [
            {
                "lat": float(t["latitude"]),
                "lng": float(t["longitude"]),
                "ts": t["recorded_at"].strftime("%Y-%m-%d %H:%M"),
                "battery": t["battery_percent"],
            }
            for t in qs
        ]
    }


def _mission_map_json(mission, assignments, waypoints) -> dict:
    robots_data = []
    for assignment in assignments:
        robot = assignment.robot
        tel = (
            robot.telemetry
            .filter(latitude__isnull=False, longitude__isnull=False)
            .order_by("-recorded_at")
            .first()
        )
        if tel:
            robots_data.append({
                "callsign": robot.callsign,
                "name": robot.name,
                "status": robot.status,
                "battery": robot.battery_percent,
                "lat": float(tel.latitude),
                "lng": float(tel.longitude),
                "url": reverse("robots:robot_detail", args=[robot.pk]),
            })

    waypoints_data = []
    for wp in waypoints:
        if wp.latitude and wp.longitude:
            waypoints_data.append({
                "order": wp.order,
                "label": wp.label or f"Waypoint {wp.order}",
                "instruction": wp.instruction,
                "lat": float(wp.latitude),
                "lng": float(wp.longitude),
            })

    return {"waypoints": waypoints_data, "robots": robots_data, "status": mission.status}


def _person(request):
    if not request.user.is_authenticated:
        return None
    person = getattr(request.user, "community_profile", None)
    if person:
        return person
    try:
        from toto.people.models import Person
        return Person.objects.filter(user=request.user).first()
    except Exception:
        return None


def _paginate(request, queryset, per_page=25):
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(request.GET.get("page"))


def _community_filter(request, queryset):
    community_id = request.GET.get("community")
    if community_id:
        return queryset.filter(community_id=community_id)
    return queryset


@login_required
def dashboard(request):
    robots = Robot.objects.select_related("community", "model", "operator", "current_location")
    missions = RobotMission.objects.select_related("community", "fleet")
    tickets = MaintenanceTicket.objects.select_related("robot", "assigned_to")

    return _render(request, "robots/dashboard.html", {
        "robot_count": robots.count(),
        "active_robot_count": robots.filter(status__in=[RobotStatus.ACTIVE, RobotStatus.RETURNING]).count(),
        "offline_robot_count": robots.filter(status=RobotStatus.OFFLINE).count(),
        "faulted_robot_count": robots.filter(status=RobotStatus.FAULTED).count(),
        "mission_count": missions.count(),
        "running_mission_count": missions.filter(status=MissionStatus.RUNNING).count(),
        "open_ticket_count": tickets.filter(
            status__in=[MaintenanceStatus.OPEN, MaintenanceStatus.TRIAGED, MaintenanceStatus.IN_PROGRESS]
        ).count(),
        "recent_robots": robots.order_by("-updated_at")[:8],
        "recent_missions": missions.order_by("-updated_at")[:8],
        "recent_events": RobotEvent.objects.select_related("robot", "mission").order_by("-occurred_at")[:12],
        "status_counts": robots.values("status").annotate(count=Count("id")).order_by("status"),
    })


@login_required
def robot_list(request):
    robots = Robot.objects.select_related("community", "model", "operator", "current_location").prefetch_related("capabilities")
    robots = _community_filter(request, robots)

    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if q:
        robots = robots.filter(
            Q(name__icontains=q) |
            Q(callsign__icontains=q) |
            Q(serial_number__icontains=q) |
            Q(model__name__icontains=q) |
            Q(model__manufacturer__icontains=q)
        )
    if status:
        robots = robots.filter(status=status)

    return _render(request, "robots/robot_list.html", {
        "robots": _paginate(request, robots.order_by("callsign")),
        "query": q,
        "status_filter": status,
        "status_choices": RobotStatus.choices,
    })


@login_required
def robot_detail(request, pk):
    robot = get_object_or_404(
        Robot.objects.select_related("community", "model", "operator", "home_base", "current_location"),
        pk=pk,
    )
    telemetry_qs = robot.telemetry.select_related("mission").order_by("-recorded_at")[:20]
    return _render(request, "robots/robot_detail.html", {
        "robot": robot,
        "assignments": robot.assignments.select_related("mission").order_by("-mission__created_at")[:20],
        "telemetry": telemetry_qs,
        "track_data": _robot_track_json(robot),
        "events": robot.events.select_related("mission", "acknowledged_by").order_by("-occurred_at")[:30],
        "tickets": robot.maintenance_tickets.select_related("component", "assigned_to").order_by("-opened_at")[:20],
        "commands": robot.commands.select_related("mission", "issued_by").order_by("-issued_at")[:20],
        "components": robot.components.order_by("component_type", "name"),
        "command_form": RobotCommandForm(initial={"robot": robot, "issued_by": _person(request)}),
        "telemetry_form": RobotTelemetryForm(initial={"robot": robot}),
    })


@login_required
def robot_create(request):
    form = RobotForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        robot = form.save()
        messages.success(request, "Robot created.")
        return redirect("robots:robot_detail", pk=robot.pk)
    return _render(request, "robots/form.html", {
        "form": form,
        "title": "New robot",
        "back_url": reverse("robots:robot_list"),
    })


@login_required
def robot_update(request, pk):
    robot = get_object_or_404(Robot, pk=pk)
    form = RobotForm(request.POST or None, instance=robot)
    if request.method == "POST" and form.is_valid():
        robot = form.save()
        messages.success(request, "Robot updated.")
        return redirect("robots:robot_detail", pk=robot.pk)
    return _render(request, "robots/form.html", {
        "form": form,
        "title": f"Edit {robot.callsign}",
        "back_url": reverse("robots:robot_detail", args=[robot.pk]),
    })


@login_required
@require_POST
def robot_commission(request, pk):
    robot = get_object_or_404(Robot, pk=pk)
    commission_robot(robot, operator=_person(request))
    messages.success(request, "Robot commissioned.")
    return redirect("robots:robot_detail", pk=robot.pk)


@login_required
@require_POST
def robot_retire(request, pk):
    robot = get_object_or_404(Robot, pk=pk)
    retire_robot(robot, reason=request.POST.get("reason", "Manual retirement."))
    messages.warning(request, "Robot retired.")
    return redirect("robots:robot_detail", pk=robot.pk)


@login_required
@require_POST
def robot_command_create(request, pk):
    robot = get_object_or_404(Robot, pk=pk)
    form = RobotCommandForm(request.POST)
    if form.is_valid():
        command = form.save(commit=False)
        command.robot = robot
        if not command.issued_by_id:
            command.issued_by = _person(request)
        command.save()
        messages.success(request, "Command queued for manual dispatch.")
    else:
        messages.error(request, "Command could not be created.")
    return redirect("robots:robot_detail", pk=robot.pk)


@login_required
@require_POST
def robot_telemetry_create(request, pk):
    robot = get_object_or_404(Robot, pk=pk)
    form = RobotTelemetryForm(request.POST)
    if form.is_valid():
        telemetry = form.save(commit=False)
        telemetry.robot = robot
        telemetry.save()
        if telemetry.battery_percent is not None:
            robot.battery_percent = telemetry.battery_percent
            robot.last_seen_at = telemetry.recorded_at
            robot.save(update_fields=["battery_percent", "last_seen_at", "updated_at"])
        messages.success(request, "Telemetry snapshot recorded.")
    else:
        messages.error(request, "Telemetry snapshot could not be recorded.")
    return redirect("robots:robot_detail", pk=robot.pk)


@login_required
def fleet_list(request):
    fleets = Fleet.objects.select_related("community", "manager").annotate(robot_count=Count("robots"))
    fleets = _community_filter(request, fleets)
    q = request.GET.get("q", "").strip()
    if q:
        fleets = fleets.filter(Q(name__icontains=q) | Q(slug__icontains=q) | Q(description__icontains=q))
    return _render(request, "robots/fleet_list.html", {
        "fleets": _paginate(request, fleets.order_by("name")),
        "query": q,
    })


@login_required
def fleet_detail(request, pk):
    fleet = get_object_or_404(Fleet.objects.select_related("community", "manager"), pk=pk)
    return _render(request, "robots/fleet_detail.html", {
        "fleet": fleet,
        "robots": fleet.robots.select_related("model", "operator", "current_location").order_by("callsign"),
        "missions": fleet.missions.select_related("community").order_by("-created_at")[:25],
        "assignment_form": RobotAssignmentForm(),
    })


@login_required
def fleet_create(request):
    form = FleetForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        fleet = form.save()
        messages.success(request, "Fleet created.")
        return redirect("robots:fleet_detail", pk=fleet.pk)
    return _render(request, "robots/form.html", {
        "form": form,
        "title": "New fleet",
        "back_url": reverse("robots:fleet_list"),
    })


@login_required
def fleet_update(request, pk):
    fleet = get_object_or_404(Fleet, pk=pk)
    form = FleetForm(request.POST or None, instance=fleet)
    if request.method == "POST" and form.is_valid():
        fleet = form.save()
        messages.success(request, "Fleet updated.")
        return redirect("robots:fleet_detail", pk=fleet.pk)
    return _render(request, "robots/form.html", {
        "form": form,
        "title": f"Edit {fleet.name}",
        "back_url": reverse("robots:fleet_detail", args=[fleet.pk]),
    })


@login_required
def mission_list(request):
    missions = RobotMission.objects.select_related(
        "community", "fleet", "origin", "destination", "route"
    ).annotate(robot_count=Count("assignments"))
    missions = _community_filter(request, missions)
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if q:
        missions = missions.filter(
            Q(title__icontains=q) | Q(description__icontains=q) | Q(fleet__name__icontains=q)
        )
    if status:
        missions = missions.filter(status=status)

    return _render(request, "robots/mission_list.html", {
        "missions": _paginate(request, missions.order_by("-created_at")),
        "query": q,
        "status_filter": status,
        "status_choices": MissionStatus.choices,
    })


@login_required
def mission_detail(request, pk):
    mission = get_object_or_404(
        RobotMission.objects.select_related(
            "community", "fleet", "requested_by", "supervisor", "origin", "destination", "route"
        ),
        pk=pk,
    )
    assignments_qs = list(mission.assignments.select_related("robot", "robot__model").order_by("robot__callsign"))
    waypoints_qs = list(mission.waypoints.select_related("address").order_by("order"))
    return _render(request, "robots/mission_detail.html", {
        "mission": mission,
        "assignments": assignments_qs,
        "waypoints": waypoints_qs,
        "mission_map_data": _mission_map_json(mission, assignments_qs, waypoints_qs),
        "events": mission.events.select_related("robot").order_by("-occurred_at")[:30],
        "commands": mission.commands.select_related("robot", "issued_by").order_by("-issued_at")[:30],
        "waypoint_form": MissionWaypointForm(initial={"mission": mission}),
        "assignment_form": RobotAssignmentForm(initial={"mission": mission}),
    })


@login_required
def mission_create(request):
    form = RobotMissionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        mission = form.save()
        messages.success(request, "Mission created.")
        return redirect("robots:mission_detail", pk=mission.pk)
    return _render(request, "robots/form.html", {
        "form": form,
        "title": "New robot mission",
        "back_url": reverse("robots:mission_list"),
    })


@login_required
def mission_update(request, pk):
    mission = get_object_or_404(RobotMission, pk=pk)
    form = RobotMissionForm(request.POST or None, instance=mission)
    if request.method == "POST" and form.is_valid():
        mission = form.save()
        messages.success(request, "Mission updated.")
        return redirect("robots:mission_detail", pk=mission.pk)
    return _render(request, "robots/form.html", {
        "form": form,
        "title": f"Edit {mission.title}",
        "back_url": reverse("robots:mission_detail", args=[mission.pk]),
    })


@login_required
@require_POST
def mission_dispatch(request, pk):
    mission = get_object_or_404(RobotMission, pk=pk)
    dispatch_mission(mission)
    messages.success(request, "Mission dispatched.")
    return redirect("robots:mission_detail", pk=mission.pk)


@login_required
@require_POST
def mission_cancel(request, pk):
    mission = get_object_or_404(RobotMission, pk=pk)
    mission.status = MissionStatus.CANCELLED
    mission.save(update_fields=["status", "updated_at"])
    mission.assignments.exclude(status=AssignmentStatus.COMPLETED).update(status=AssignmentStatus.CANCELLED)
    messages.warning(request, "Mission cancelled.")
    return redirect("robots:mission_detail", pk=mission.pk)


@login_required
@require_POST
def mission_assignment_create(request, pk):
    mission = get_object_or_404(RobotMission, pk=pk)
    form = RobotAssignmentForm(request.POST)
    if form.is_valid():
        assignment = form.save(commit=False)
        assignment.mission = mission
        assignment.save()
        messages.success(request, "Robot assigned to mission.")
    else:
        messages.error(request, "Robot assignment could not be created.")
    return redirect("robots:mission_detail", pk=mission.pk)


@login_required
@require_POST
def mission_waypoint_create(request, pk):
    mission = get_object_or_404(RobotMission, pk=pk)
    form = MissionWaypointForm(request.POST)
    if form.is_valid():
        waypoint = form.save(commit=False)
        waypoint.mission = mission
        waypoint.save()
        messages.success(request, "Waypoint added.")
    else:
        messages.error(request, "Waypoint could not be added.")
    return redirect("robots:mission_detail", pk=mission.pk)


@login_required
def maintenance_list(request):
    tickets = MaintenanceTicket.objects.select_related("robot", "component", "assigned_to").order_by("-opened_at")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if q:
        tickets = tickets.filter(
            Q(title__icontains=q) | Q(description__icontains=q) | Q(robot__callsign__icontains=q)
        )
    if status:
        tickets = tickets.filter(status=status)
    return _render(request, "robots/maintenance_list.html", {
        "tickets": _paginate(request, tickets),
        "query": q,
        "status_filter": status,
        "status_choices": MaintenanceStatus.choices,
    })


@login_required
def maintenance_create(request):
    form = MaintenanceTicketForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        ticket = form.save()
        messages.success(request, "Maintenance ticket created.")
        return redirect("robots:robot_detail", pk=ticket.robot_id)
    return _render(request, "robots/form.html", {
        "form": form,
        "title": "New maintenance ticket",
        "back_url": reverse("robots:maintenance_list"),
    })


@login_required
def map_view(request):
    payload = robots_map_payload()
    return _render(request, "robots/map.html", {
        "map_payload_json": json.dumps(payload),
        "robot_count": len(payload["robots"]),
        "mission_count": len(payload["missions"]),
        "dock_count": len(payload["docks"]),
    })


@login_required
def map_api(request):
    return JsonResponse(robots_map_payload())


@login_required
def model_list(request):
    models = RobotModel.objects.order_by("manufacturer", "name")
    return _render(request, "robots/model_list.html", {"models": models})


@login_required
def model_create(request):
    form = RobotModelForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        model = form.save()
        messages.success(request, "Robot model created.")
        return redirect("robots:model_list")
    return _render(request, "robots/form.html", {
        "form": form,
        "title": "New robot model",
        "back_url": reverse("robots:model_list"),
    })


@login_required
def dock_list(request):
    docks = ChargingDock.objects.select_related("community", "location").order_by("name")
    return _render(request, "robots/dock_list.html", {"docks": docks})


@login_required
def metrics_view(request):
    from .metrics import update_metrics
    try:
        update_metrics()
    except Exception:
        pass

    return _render(request, "robots/metrics.html", {
        "stats": overview_stats(),
        "robot_status_data": robot_status_chart_data(),
        "mission_status_data": mission_status_chart_data(),
        "missions_over_time_data": missions_over_time_chart_data(),
        "battery_distribution_data": battery_distribution_chart_data(),
        "events_severity_data": events_by_severity_chart_data(),
    })


@login_required
def dock_create(request):
    form = ChargingDockForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        dock = form.save()
        messages.success(request, "Charging dock created.")
        return redirect("robots:dock_list")
    return _render(request, "robots/form.html", {
        "form": form,
        "title": "New charging dock",
        "back_url": reverse("robots:dock_list"),
    })
