from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from django.urls import reverse

from .models import ChargingDock, Fleet, Robot, RobotMission


def geometry_json(geometry) -> dict[str, Any] | None:
    if not geometry:
        return None
    try:
        return json.loads(geometry.geojson)
    except Exception:
        return None


def address_geometry(address) -> dict[str, Any] | None:
    if not address:
        return None
    geometry = getattr(address, "geometry", None) or getattr(address, "point", None)
    return geometry_json(geometry)


def address_label(address) -> str:
    return str(address) if address else ""


def decimal_to_float(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return value


def route_payload(route):
    if not route:
        return None
    geometry = getattr(route, "geometry", None) or getattr(route, "linestring", None)
    return {
        "id": route.pk,
        "name": getattr(route, "name", "") or f"Route {route.pk}",
        "geometry": geometry_json(geometry),
    }


def robot_payload(robot: Robot) -> dict[str, Any]:
    return {
        "id": robot.pk,
        "uuid": str(robot.uuid),
        "name": robot.name,
        "callsign": robot.callsign,
        "status": robot.status,
        "status_label": robot.get_status_display(),
        "battery_percent": robot.battery_percent,
        "is_active": robot.is_active,
        "is_autonomous": robot.is_autonomous,
        "model": str(robot.model) if robot.model_id else "",
        "kind": robot.model.kind if robot.model_id else "",
        "operator": str(robot.operator) if robot.operator_id else "",
        "community": str(robot.community) if robot.community_id else "",
        "current_location": address_label(robot.current_location),
        "home_base": address_label(robot.home_base),
        "geometry": address_geometry(robot.current_location),
        "home_geometry": address_geometry(robot.home_base),
        "last_seen_at": robot.last_seen_at.isoformat() if robot.last_seen_at else None,
        "detail_url": reverse("robots:robot_detail", args=[robot.pk]),
    }


def waypoint_payload(waypoint) -> dict[str, Any]:
    return {
        "id": waypoint.pk,
        "order": waypoint.order,
        "label": waypoint.label or f"Waypoint {waypoint.order}",
        "instruction": waypoint.instruction,
        "lat": decimal_to_float(waypoint.latitude),
        "lng": decimal_to_float(waypoint.longitude),
        "altitude_m": decimal_to_float(waypoint.altitude_m),
        "address": address_label(waypoint.address),
        "geometry": address_geometry(waypoint.address),
    }


def mission_payload(mission: RobotMission) -> dict[str, Any]:
    assignments = list(getattr(mission, "prefetched_assignments", []) or mission.assignments.select_related("robot")[:50])
    waypoints = list(getattr(mission, "prefetched_waypoints", []) or mission.waypoints.select_related("address").order_by("order")[:100])

    return {
        "id": mission.pk,
        "uuid": str(mission.uuid),
        "title": mission.title,
        "status": mission.status,
        "status_label": mission.get_status_display(),
        "priority": mission.priority,
        "community": str(mission.community) if mission.community_id else "",
        "fleet": str(mission.fleet) if mission.fleet_id else "",
        "origin": address_label(mission.origin),
        "destination": address_label(mission.destination),
        "origin_geometry": address_geometry(mission.origin),
        "destination_geometry": address_geometry(mission.destination),
        "route": route_payload(mission.route),
        "waypoints": [waypoint_payload(w) for w in waypoints],
        "robots": [
            {
                "id": assignment.robot_id,
                "callsign": assignment.robot.callsign,
                "name": assignment.robot.name,
                "status": assignment.status,
                "assignment_status_label": assignment.get_status_display(),
            }
            for assignment in assignments
            if assignment.robot_id
        ],
        "scheduled_start_at": mission.scheduled_start_at.isoformat() if mission.scheduled_start_at else None,
        "scheduled_end_at": mission.scheduled_end_at.isoformat() if mission.scheduled_end_at else None,
        "detail_url": reverse("robots:mission_detail", args=[mission.pk]),
    }


def dock_payload(dock: ChargingDock) -> dict[str, Any]:
    return {
        "id": dock.pk,
        "uuid": str(dock.uuid),
        "name": dock.name,
        "community": str(dock.community) if dock.community_id else "",
        "location": address_label(dock.location),
        "geometry": address_geometry(dock.location),
        "power_kw": decimal_to_float(dock.power_kw),
        "slot_count": dock.slot_count,
        "is_active": dock.is_active,
    }


def fleet_payload(fleet: Fleet) -> dict[str, Any]:
    return {
        "id": fleet.pk,
        "uuid": str(fleet.uuid),
        "name": fleet.name,
        "slug": fleet.slug,
        "community": str(fleet.community) if fleet.community_id else "",
        "manager": str(fleet.manager) if fleet.manager_id else "",
        "is_active": fleet.is_active,
        "robot_count": getattr(fleet, "robot_count", None) or fleet.robots.count(),
        "detail_url": reverse("robots:fleet_detail", args=[fleet.pk]),
    }


def robots_map_payload(robot_qs=None, mission_qs=None, dock_qs=None) -> dict[str, Any]:
    robots = robot_qs if robot_qs is not None else Robot.objects.all()
    missions = mission_qs if mission_qs is not None else RobotMission.objects.all()
    docks = dock_qs if dock_qs is not None else ChargingDock.objects.all()

    robots = robots.select_related("community", "model", "operator", "home_base", "current_location")
    missions = missions.select_related(
        "community", "fleet", "origin", "destination", "route"
    ).prefetch_related("waypoints__address", "assignments__robot")
    docks = docks.select_related("community", "location")

    return {
        "robots": [robot_payload(robot) for robot in robots],
        "missions": [mission_payload(mission) for mission in missions],
        "docks": [dock_payload(dock) for dock in docks],
    }
