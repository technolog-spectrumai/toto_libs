from __future__ import annotations

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Community

from ..models import (
    AssignmentStatus,
    ChargingDock,
    EventSeverity,
    Fleet,
    FleetMembership,
    MaintenanceStatus,
    MissionStatus,
    Robot,
    RobotAssignment,
    RobotCapability,
    RobotCommand,
    RobotEvent,
    RobotKind,
    RobotMission,
    RobotModel,
    RobotStatus,
    RobotTelemetry,
)
from ..selectors import robots_map_payload
from ..services import (
    acknowledge_command,
    assign_robot,
    close_maintenance_ticket,
    commission_robot,
    complete_assignment,
    complete_command,
    dispatch_mission,
    end_charging_session,
    issue_command,
    open_maintenance_ticket,
    record_robot_event,
    record_telemetry,
    retire_robot,
    start_assignment,
    start_charging_session,
)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def make_community(name="Test Community"):
    return Community.objects.create(name=name)


def make_user(username="testuser"):
    return User.objects.create_user(username=username, password="pass")


def make_person(username="testuser"):
    user = make_user(username)
    return Person.objects.create(display_name=username, user=user)


def make_robot_model(manufacturer="Acme", name="Scout", kind=RobotKind.GROUND):
    from django.utils.text import slugify
    return RobotModel.objects.create(
        manufacturer=manufacturer,
        name=name,
        slug=slugify(f"{manufacturer}-{name}"),
        kind=kind,
    )


def make_robot(community=None, model=None, callsign="r-001"):
    if community is None:
        community = make_community()
    if model is None:
        model = make_robot_model()
    return Robot.objects.create(
        community=community,
        model=model,
        name="Test Robot",
        callsign=callsign,
        status=RobotStatus.IDLE,
    )


def make_mission(community=None, fleet=None):
    if community is None:
        community = make_community("Mission Community")
    return RobotMission.objects.create(
        community=community,
        fleet=fleet,
        title="Test Mission",
        status=MissionStatus.PLANNED,
    )


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class RobotModelModelTest(TestCase):
    def test_str(self):
        m = make_robot_model()
        assert str(m) == "Acme Scout"

    def test_unique_manufacturer_name(self):
        make_robot_model()
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            RobotModel.objects.create(
                manufacturer="Acme",
                name="Scout",
                slug="acme-scout-2",
                kind=RobotKind.GROUND,
            )


class RobotCapabilityTest(TestCase):
    def test_create_and_str(self):
        cap = RobotCapability.objects.create(name="Mapping", slug="mapping")
        assert str(cap) == "Mapping"


class RobotTest(TestCase):
    def test_str(self):
        robot = make_robot(callsign="alpha")
        assert "alpha" in str(robot)

    def test_battery_validation(self):
        robot = make_robot()
        robot.battery_percent = 150
        with self.assertRaises(ValidationError):
            robot.clean()

    def test_retired_status_validation(self):
        from django.utils import timezone
        robot = make_robot()
        robot.retired_at = timezone.now()
        robot.status = RobotStatus.IDLE
        with self.assertRaises(ValidationError):
            robot.clean()

    def test_unique_callsign_per_community(self):
        community = make_community()
        model = make_robot_model()
        Robot.objects.create(community=community, model=model, name="R1", callsign="same")
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            Robot.objects.create(community=community, model=model, name="R2", callsign="same")


class FleetTest(TestCase):
    def test_create(self):
        community = make_community()
        fleet = Fleet.objects.create(community=community, name="Alpha Fleet", slug="alpha-fleet")
        assert str(fleet) == "Alpha Fleet"

    def test_unique_slug_per_community(self):
        community = make_community()
        Fleet.objects.create(community=community, name="A", slug="same")
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            Fleet.objects.create(community=community, name="B", slug="same")


class FleetMembershipTest(TestCase):
    def test_cross_community_validation(self):
        c1 = make_community("C1")
        c2 = make_community("C2")
        model = make_robot_model()
        robot = Robot.objects.create(community=c1, model=model, name="R1", callsign="r1")
        fleet = Fleet.objects.create(community=c2, name="F", slug="f")
        membership = FleetMembership(fleet=fleet, robot=robot)
        with self.assertRaises(ValidationError):
            membership.clean()


class RobotMissionTest(TestCase):
    def test_str(self):
        mission = make_mission()
        assert mission.title in str(mission)

    def test_scheduled_end_before_start(self):
        from django.utils import timezone
        from datetime import timedelta
        mission = make_mission()
        now = timezone.now()
        mission.scheduled_start_at = now
        mission.scheduled_end_at = now - timedelta(hours=1)
        with self.assertRaises(ValidationError):
            mission.clean()


class RobotTelemetryTest(TestCase):
    def test_battery_validation(self):
        robot = make_robot()
        telemetry = RobotTelemetry(robot=robot, battery_percent=110)
        with self.assertRaises(ValidationError):
            telemetry.clean()


# ---------------------------------------------------------------------------
# Services tests
# ---------------------------------------------------------------------------

class CommissionRetireTest(TestCase):
    def test_commission(self):
        robot = make_robot()
        robot.status = RobotStatus.PROVISIONING
        robot.save()
        commission_robot(robot)
        robot.refresh_from_db()
        assert robot.status == RobotStatus.IDLE
        assert robot.is_active is True
        assert robot.commissioned_at is not None

    def test_retire(self):
        robot = make_robot()
        retire_robot(robot, reason="End of life")
        robot.refresh_from_db()
        assert robot.status == RobotStatus.RETIRED
        assert robot.is_active is False
        assert robot.retired_at is not None

    def test_retire_cancels_assignments(self):
        community = make_community()
        robot = make_robot(community=community)
        mission = make_mission(community=community)
        assignment = RobotAssignment.objects.create(
            mission=mission, robot=robot, status=AssignmentStatus.RUNNING
        )
        retire_robot(robot)
        assignment.refresh_from_db()
        assert assignment.status == AssignmentStatus.CANCELLED


class AssignDispatchTest(TestCase):
    def test_assign_robot(self):
        community = make_community()
        robot = make_robot(community=community)
        mission = make_mission(community=community)
        assignment = assign_robot(mission, robot, role="lead")
        assert assignment.mission == mission
        assert assignment.robot == robot

    def test_assign_cross_community_raises(self):
        c1 = make_community("C1")
        c2 = make_community("C2")
        robot = make_robot(community=c1)
        mission = make_mission(community=c2)
        with self.assertRaises(ValueError):
            assign_robot(mission, robot)

    def test_dispatch_mission(self):
        community = make_community()
        robot = make_robot(community=community)
        mission = make_mission(community=community)
        assign_robot(mission, robot)
        dispatch_mission(mission)
        mission.refresh_from_db()
        assert mission.status == MissionStatus.DISPATCHED

    def test_start_and_complete_assignment(self):
        community = make_community()
        robot = make_robot(community=community)
        mission = make_mission(community=community)
        assignment = assign_robot(mission, robot)
        start_assignment(assignment)
        assignment.refresh_from_db()
        assert assignment.status == AssignmentStatus.RUNNING
        complete_assignment(assignment)
        assignment.refresh_from_db()
        assert assignment.status == AssignmentStatus.COMPLETED
        mission.refresh_from_db()
        assert mission.status == MissionStatus.COMPLETED


class TelemetryTest(TestCase):
    def test_record_telemetry_updates_robot(self):
        robot = make_robot()
        record_telemetry(robot, {"battery_percent": 75, "latitude": "51.5", "longitude": "-0.1"})
        robot.refresh_from_db()
        assert robot.battery_percent == 75
        assert robot.last_seen_at is not None


class EventTest(TestCase):
    def test_record_event(self):
        robot = make_robot()
        event = record_robot_event(robot, "test.event", EventSeverity.WARNING, "Test message")
        assert event.pk is not None
        assert event.severity == EventSeverity.WARNING
        assert event.event_type == "test.event"


class CommandTest(TestCase):
    def test_issue_and_acknowledge(self):
        robot = make_robot()
        cmd = issue_command(robot, "move", {"x": 1})
        assert cmd.pk is not None
        acknowledge_command(cmd, {"status": "ok"})
        cmd.refresh_from_db()
        assert cmd.acknowledged_at is not None

    def test_complete_command(self):
        robot = make_robot()
        cmd = issue_command(robot, "stop")
        complete_command(cmd, success=True)
        cmd.refresh_from_db()
        assert cmd.success is True
        assert cmd.completed_at is not None

    def test_idempotency_key_deduplication(self):
        robot = make_robot()
        cmd1 = issue_command(robot, "ping", idempotency_key="key-1")
        cmd2 = issue_command(robot, "ping", idempotency_key="key-1")
        assert cmd1.pk == cmd2.pk


class MaintenanceTest(TestCase):
    def test_open_and_close_ticket(self):
        robot = make_robot()
        robot.status = RobotStatus.ACTIVE
        robot.save()
        ticket = open_maintenance_ticket(robot, "Motor failure", severity=EventSeverity.CRITICAL)
        robot.refresh_from_db()
        assert robot.status == RobotStatus.FAULTED
        close_maintenance_ticket(ticket)
        ticket.refresh_from_db()
        assert ticket.status == MaintenanceStatus.CLOSED


class ChargingTest(TestCase):
    def test_start_end_session(self):
        robot = make_robot()
        session = start_charging_session(robot)
        robot.refresh_from_db()
        assert robot.status == RobotStatus.CHARGING
        end_charging_session(session, end_battery_percent=95)
        robot.refresh_from_db()
        assert robot.battery_percent == 95
        assert robot.status == RobotStatus.IDLE


# ---------------------------------------------------------------------------
# Selectors tests
# ---------------------------------------------------------------------------

class MapPayloadTest(TestCase):
    def test_empty(self):
        payload = robots_map_payload()
        assert "robots" in payload
        assert "missions" in payload
        assert "docks" in payload

    def test_robot_in_payload(self):
        make_robot()
        payload = robots_map_payload()
        assert len(payload["robots"]) == 1
        robot_data = payload["robots"][0]
        assert "callsign" in robot_data
        assert "status" in robot_data
        assert "detail_url" in robot_data

    def test_dock_in_payload(self):
        community = make_community()
        ChargingDock.objects.create(community=community, name="Dock A", slug="dock-a")
        payload = robots_map_payload()
        assert len(payload["docks"]) == 1


# ---------------------------------------------------------------------------
# URL / view smoke tests
# ---------------------------------------------------------------------------

class ViewSmokeTest(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Test", author="Test", publication_year=2024, active=True)
        self.user = make_user("viewuser")
        self.client.login(username="viewuser", password="pass")

    def _get(self, name, *args):
        return self.client.get(reverse(f"robots:{name}", args=args))

    def test_dashboard(self):
        assert self._get("dashboard").status_code == 200

    def test_robot_list(self):
        assert self._get("robot_list").status_code == 200

    def test_fleet_list(self):
        assert self._get("fleet_list").status_code == 200

    def test_mission_list(self):
        assert self._get("mission_list").status_code == 200

    def test_maintenance_list(self):
        assert self._get("maintenance_list").status_code == 200

    def test_model_list(self):
        assert self._get("model_list").status_code == 200

    def test_dock_list(self):
        assert self._get("dock_list").status_code == 200

    def test_map_view(self):
        assert self._get("map").status_code == 200

    def test_map_api(self):
        response = self._get("map_api")
        assert response.status_code == 200
        data = response.json()
        assert "robots" in data

    def test_robot_detail(self):
        robot = make_robot()
        assert self._get("robot_detail", robot.pk).status_code == 200

    def test_fleet_detail(self):
        fleet = Fleet.objects.create(community=make_community(), name="F", slug="f")
        assert self._get("fleet_detail", fleet.pk).status_code == 200

    def test_mission_detail(self):
        mission = make_mission()
        assert self._get("mission_detail", mission.pk).status_code == 200

    def test_unauthenticated_redirects(self):
        self.client.logout()
        response = self._get("dashboard")
        assert response.status_code == 302

    def test_robot_create_get(self):
        assert self._get("robot_create").status_code == 200

    def test_mission_create_get(self):
        assert self._get("mission_create").status_code == 200

    def test_metrics(self):
        assert self._get("metrics").status_code == 200
