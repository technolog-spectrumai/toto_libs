from __future__ import annotations


def robot_graph_node(robot):
    return {
        "label": "Robot",
        "key": f"robot:{robot.pk}",
        "properties": {
            "uuid": str(robot.uuid),
            "callsign": robot.callsign,
            "name": robot.name,
            "status": robot.status,
            "battery_percent": robot.battery_percent,
        },
    }


def mission_graph_node(mission):
    return {
        "label": "RobotMission",
        "key": f"robot_mission:{mission.pk}",
        "properties": {
            "uuid": str(mission.uuid),
            "title": mission.title,
            "status": mission.status,
            "priority": mission.priority,
        },
    }
