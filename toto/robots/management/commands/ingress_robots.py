from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from django.utils.text import slugify

from toto.ingress import IngressCommand

from ...models import (
    AssignmentStatus,
    ChargingDock,
    ChargingSession,
    ComponentReading,
    EventSeverity,
    FirmwareInstallation,
    FirmwareRelease,
    Fleet,
    FleetMembership,
    MaintenanceAction,
    MaintenanceStatus,
    MaintenanceTicket,
    MissionStatus,
    MissionWaypoint,
    RobotAssignment,
    RobotCapability,
    RobotCommand,
    RobotComponent,
    RobotEvent,
    RobotKind,
    RobotMission,
    RobotModel,
    RobotStatus,
    RobotTelemetry,
)
from ...models import Robot


DEFAULT_CAPABILITIES = [
    ("Mapping", "mapping", "Builds maps or scans terrain."),
    ("Inspection", "inspection", "Inspects structures, routes, or equipment."),
    ("Delivery", "delivery", "Moves packages or payloads."),
    ("Patrol", "patrol", "Performs patrol or observation loops."),
    ("Lifting", "lifting", "Carries or lifts equipment."),
    ("Thermal Sensing", "thermal_sensing", "Captures thermal data."),
    ("Hazard Detection", "hazard_detection", "Detects hazards, threats, or anomalies."),
    ("Remote Manipulation", "remote_manipulation", "Manipulates objects remotely."),
    ("Inventory Scan", "inventory_scan", "Scans inventory tags, QR codes, or shelves."),
    ("Emergency Response", "emergency_response", "Supports emergency operations."),
    ("Underwater Navigation", "underwater_nav", "Navigates and operates in submerged environments."),
    ("Air Quality Monitoring", "air_quality", "Measures and logs atmospheric composition data."),
    ("Structural Analysis", "structural_analysis", "Performs non-destructive structural testing."),
    ("Swarm Coordination", "swarm_coord", "Participates in multi-agent coordination protocols."),
]

ROBOT_MODEL_SPECS = [
    dict(
        manufacturer="Toto Robotics",
        name="Scout Rover",
        kind=RobotKind.GROUND,
        payload_capacity_kg=Decimal("5.000"),
        max_speed_mps=Decimal("2.500"),
        battery_capacity_wh=480,
        nominal_runtime_minutes=180,
        description="Lightweight ground reconnaissance robot for patrol and mapping operations.",
        supported_capabilities=["mapping", "patrol", "hazard_detection"],
    ),
    dict(
        manufacturer="Toto Robotics",
        name="Courier Drone",
        kind=RobotKind.AERIAL,
        payload_capacity_kg=Decimal("2.200"),
        max_speed_mps=Decimal("12.000"),
        battery_capacity_wh=220,
        nominal_runtime_minutes=45,
        description="Fast aerial delivery drone with GPS waypoint navigation.",
        supported_capabilities=["delivery", "mapping", "inspection"],
    ),
    dict(
        manufacturer="Toto Robotics",
        name="Warehouse Arm",
        kind=RobotKind.ARM,
        payload_capacity_kg=Decimal("25.000"),
        max_speed_mps=None,
        battery_capacity_wh=None,
        nominal_runtime_minutes=None,
        description="Fixed-mount robotic arm for inventory handling and sorting.",
        supported_capabilities=["lifting", "inventory_scan", "remote_manipulation"],
    ),
    dict(
        manufacturer="Horizon Marine Systems",
        name="HM-300 Aquadrone",
        kind=RobotKind.MARINE,
        payload_capacity_kg=Decimal("8.500"),
        max_speed_mps=Decimal("4.200"),
        battery_capacity_wh=960,
        nominal_runtime_minutes=300,
        description="Surface-level marine drone for coastal survey and monitoring missions.",
        supported_capabilities=["mapping", "inspection", "air_quality"],
    ),
    dict(
        manufacturer="Depth Systems",
        name="Sub-7 Crawler",
        kind=RobotKind.UNDERWATER,
        payload_capacity_kg=Decimal("12.000"),
        max_speed_mps=Decimal("1.800"),
        battery_capacity_wh=1200,
        nominal_runtime_minutes=240,
        description="Tethered underwater crawler for pipeline and infrastructure inspection.",
        supported_capabilities=["underwater_nav", "inspection", "structural_analysis"],
    ),
    dict(
        manufacturer="AstroMech Labs",
        name="Titan-H1",
        kind=RobotKind.HUMANOID,
        payload_capacity_kg=Decimal("10.000"),
        max_speed_mps=Decimal("1.400"),
        battery_capacity_wh=800,
        nominal_runtime_minutes=120,
        description="Bipedal humanoid assistant for complex manipulation tasks in unstructured environments.",
        supported_capabilities=["remote_manipulation", "lifting", "emergency_response"],
    ),
    dict(
        manufacturer="SensorNet",
        name="AirSense Node",
        kind=RobotKind.SENSOR,
        payload_capacity_kg=None,
        max_speed_mps=Decimal("1.000"),
        battery_capacity_wh=120,
        nominal_runtime_minutes=720,
        description="Long-endurance mobile sensor platform for environmental monitoring.",
        supported_capabilities=["air_quality", "thermal_sensing", "mapping"],
    ),
    dict(
        manufacturer="ClearPath Robotics",
        name="Jackal UGV",
        kind=RobotKind.GROUND,
        payload_capacity_kg=Decimal("20.000"),
        max_speed_mps=Decimal("2.000"),
        battery_capacity_wh=370,
        nominal_runtime_minutes=150,
        description="Rugged all-terrain UGV platform designed for harsh outdoor environments.",
        supported_capabilities=["patrol", "mapping", "swarm_coord"],
    ),
    dict(
        manufacturer="AstroMech Labs",
        name="VisionBot 4S",
        kind=RobotKind.SENSOR,
        payload_capacity_kg=None,
        max_speed_mps=Decimal("0.800"),
        battery_capacity_wh=90,
        nominal_runtime_minutes=480,
        description="Pan-tilt vision platform with thermal and optical dual-sensor heads.",
        supported_capabilities=["inspection", "thermal_sensing", "hazard_detection"],
    ),
]


class Command(IngressCommand):
    help = "Seed the Robots app with demo fleet data for user testing."

    def process(self):
        self.stdout.write("🤖  Seeding Robots fleet data…")

        capabilities = self._seed_capabilities()
        models       = self._seed_models(capabilities)
        docks        = self._seed_docks(models)
        people       = self._load_people()
        community    = self._load_community()

        robots   = self._seed_robots(models, capabilities, people, community)
        fleets   = self._seed_fleets(robots, people, community)
        missions = self._seed_missions(robots, fleets, people, community)

        self._seed_telemetry(robots, missions)
        self._seed_events(robots, missions, people)
        self._seed_maintenance(robots, people)
        self._seed_components(robots)
        self._seed_firmware(models, robots, people)
        self._seed_charging_sessions(robots, docks)
        self._seed_commands(robots, missions, people)

        self.stdout.write(self.style.SUCCESS("✅  Robots ingress complete."))

    # ------------------------------------------------------------------ helpers

    def _load_community(self):
        from toto.socialhub.models import Community
        return {
            "main":  Community.objects.get(pk=1),
            "north": Community.objects.get(pk=2),
            "south": Community.objects.get(pk=3),
        }

    def _load_people(self):
        from toto.people.models import Person
        ids = {
            "founder":  2,
            "manager1": 3,
            "manager2": 4,
            "staff3":   5,
            "staff4":   6,
            "staff5":   7,
            "staff6":   8,
            "ada":      10,
            "marek":    11,
            "ewa":      14,
            "piotr":    15,
        }
        result = {}
        for key, pk in ids.items():
            try:
                result[key] = Person.objects.get(pk=pk)
            except Person.DoesNotExist:
                result[key] = None
        return result

    def _ok(self, label, created):
        if created:
            self.stdout.write(f"  + {label}")
        else:
            self.stdout.write(self.style.WARNING(f"  ⚠ skipped existing {label}"))

    # ------------------------------------------------------------------ capabilities

    def _seed_capabilities(self):
        caps = {}
        for name, slug, description in DEFAULT_CAPABILITIES:
            cap, created = RobotCapability.objects.get_or_create(
                slug=slug, defaults={"name": name, "description": description}
            )
            caps[slug] = cap
            self._ok(f"capability '{name}'", created)
        return caps

    # ------------------------------------------------------------------ models

    def _seed_models(self, capabilities):
        models = {}
        for spec in ROBOT_MODEL_SPECS:
            cap_slugs = spec.pop("supported_capabilities", [])
            slug = slugify(f"{spec['manufacturer']}-{spec['name']}")
            model, created = RobotModel.objects.get_or_create(
                manufacturer=spec["manufacturer"],
                name=spec["name"],
                defaults={**spec, "slug": slug},
            )
            models[model.name] = model
            self._ok(f"model '{model}'", created)
        return models

    # ------------------------------------------------------------------ docks

    def _seed_docks(self, models):
        community_main = None
        try:
            from toto.socialhub.models import Community
            community_main = Community.objects.get(pk=1)
        except Exception:
            return {}

        dock_specs = [
            dict(
                name="Main Hangar Dock A",
                slug="main-hangar-dock-a",
                power_kw=Decimal("7.400"),
                slot_count=6,
                is_active=True,
                model_names=["Scout Rover", "Jackal UGV"],
            ),
            dict(
                name="Aerial Pad Charging Bay",
                slug="aerial-pad-charging-bay",
                power_kw=Decimal("3.200"),
                slot_count=4,
                is_active=True,
                model_names=["Courier Drone", "AirSense Node", "VisionBot 4S"],
            ),
            dict(
                name="Warehouse Station W1",
                slug="warehouse-station-w1",
                power_kw=Decimal("22.000"),
                slot_count=2,
                is_active=True,
                model_names=["Warehouse Arm"],
            ),
            dict(
                name="North Field Charging Post",
                slug="north-field-charging-post",
                power_kw=Decimal("5.500"),
                slot_count=3,
                is_active=True,
                model_names=["Scout Rover", "Jackal UGV", "Courier Drone"],
            ),
        ]

        docks = {}
        for spec in dock_specs:
            model_names = spec.pop("model_names", [])
            dock, created = ChargingDock.objects.get_or_create(
                community=community_main,
                slug=spec["slug"],
                defaults={**spec, "community": community_main},
            )
            for mn in model_names:
                if mn in {m.name for m in models.values()}:
                    m = next((v for v in models.values() if v.name == mn), None)
                    if m:
                        dock.supported_models.add(m)
            docks[spec["slug"]] = dock
            self._ok(f"dock '{dock.name}'", created)
        return docks

    # ------------------------------------------------------------------ robots

    def _seed_robots(self, models, capabilities, people, community):
        now = timezone.now()

        def _model(name):
            return models.get(name)

        specs = [
            # ── Community 1 — Our Thing Inc. ──
            dict(
                community=community["main"],
                model=_model("Scout Rover"),
                name="Alpha Ground Unit",
                callsign="alpha-1",
                serial_number="TR-SR-0001",
                status=RobotStatus.ACTIVE,
                battery_percent=78,
                firmware_version="2.2.0",
                hardware_revision="Rev-C",
                is_autonomous=True,
                operator=people.get("ewa"),
                commissioned_at=now - timedelta(days=180),
                last_seen_at=now - timedelta(minutes=5),
                cap_slugs=["mapping", "patrol", "hazard_detection"],
            ),
            dict(
                community=community["main"],
                model=_model("Scout Rover"),
                name="Bravo Ground Unit",
                callsign="bravo-1",
                serial_number="TR-SR-0002",
                status=RobotStatus.IDLE,
                battery_percent=95,
                firmware_version="2.2.0",
                hardware_revision="Rev-C",
                is_autonomous=True,
                operator=people.get("ewa"),
                commissioned_at=now - timedelta(days=160),
                last_seen_at=now - timedelta(minutes=40),
                cap_slugs=["patrol", "hazard_detection"],
            ),
            dict(
                community=community["main"],
                model=_model("Courier Drone"),
                name="Charlie Aerial",
                callsign="charlie-a1",
                serial_number="TR-CD-0001",
                status=RobotStatus.ACTIVE,
                battery_percent=62,
                firmware_version="1.5.0",
                hardware_revision="Rev-B",
                is_autonomous=True,
                operator=people.get("marek"),
                commissioned_at=now - timedelta(days=90),
                last_seen_at=now - timedelta(minutes=2),
                cap_slugs=["delivery", "mapping", "inspection"],
            ),
            dict(
                community=community["main"],
                model=_model("Courier Drone"),
                name="Delta Aerial",
                callsign="delta-a1",
                serial_number="TR-CD-0002",
                status=RobotStatus.RETURNING,
                battery_percent=41,
                firmware_version="1.5.0",
                hardware_revision="Rev-B",
                is_autonomous=True,
                operator=people.get("marek"),
                commissioned_at=now - timedelta(days=85),
                last_seen_at=now - timedelta(minutes=10),
                cap_slugs=["delivery", "mapping"],
            ),
            dict(
                community=community["main"],
                model=_model("HM-300 Aquadrone"),
                name="Echo Marine",
                callsign="echo-m1",
                serial_number="HMS-300-0001",
                status=RobotStatus.CHARGING,
                battery_percent=22,
                firmware_version="3.0.1",
                hardware_revision="Rev-A",
                is_autonomous=True,
                operator=people.get("ada"),
                commissioned_at=now - timedelta(days=60),
                last_seen_at=now - timedelta(hours=2),
                cap_slugs=["mapping", "inspection", "air_quality"],
            ),
            dict(
                community=community["main"],
                model=_model("Warehouse Arm"),
                name="Foxtrot Arm Unit",
                callsign="foxtrot-arm1",
                serial_number="TR-WA-0001",
                status=RobotStatus.IDLE,
                battery_percent=100,
                firmware_version="4.1.2",
                hardware_revision="Rev-D",
                is_autonomous=False,
                operator=people.get("staff3"),
                commissioned_at=now - timedelta(days=365),
                last_seen_at=now - timedelta(hours=1),
                cap_slugs=["lifting", "inventory_scan", "remote_manipulation"],
            ),
            dict(
                community=community["main"],
                model=_model("Titan-H1"),
                name="Golf Humanoid",
                callsign="golf-h1",
                serial_number="AML-T1-0001",
                status=RobotStatus.FAULTED,
                battery_percent=12,
                firmware_version="1.2.0",
                hardware_revision="Rev-A",
                is_autonomous=True,
                operator=people.get("piotr"),
                commissioned_at=now - timedelta(days=45),
                last_seen_at=now - timedelta(hours=6),
                cap_slugs=["remote_manipulation", "lifting", "emergency_response"],
            ),
            dict(
                community=community["main"],
                model=_model("AirSense Node"),
                name="Hotel Sensor Platform",
                callsign="hotel-s1",
                serial_number="SN-AS-0001",
                status=RobotStatus.MAINTENANCE,
                battery_percent=88,
                firmware_version="2.0.0",
                hardware_revision="Rev-B",
                is_autonomous=True,
                operator=people.get("staff4"),
                commissioned_at=now - timedelta(days=200),
                last_seen_at=now - timedelta(days=2),
                cap_slugs=["air_quality", "thermal_sensing", "mapping"],
            ),
            # ── Community 2 — North Chapter ──
            dict(
                community=community["north"],
                model=_model("Scout Rover"),
                name="November Ground 1",
                callsign="nov-g1",
                serial_number="TR-SR-0010",
                status=RobotStatus.ACTIVE,
                battery_percent=71,
                firmware_version="2.2.0",
                hardware_revision="Rev-C",
                is_autonomous=True,
                operator=people.get("manager1"),
                commissioned_at=now - timedelta(days=120),
                last_seen_at=now - timedelta(minutes=15),
                cap_slugs=["mapping", "patrol"],
            ),
            dict(
                community=community["north"],
                model=_model("Jackal UGV"),
                name="November UGV 2",
                callsign="nov-ugv2",
                serial_number="CPR-J-0001",
                status=RobotStatus.IDLE,
                battery_percent=98,
                firmware_version="1.3.0",
                hardware_revision="Rev-A",
                is_autonomous=True,
                operator=people.get("manager1"),
                commissioned_at=now - timedelta(days=100),
                last_seen_at=now - timedelta(hours=3),
                cap_slugs=["patrol", "mapping", "swarm_coord"],
            ),
            dict(
                community=community["north"],
                model=_model("Courier Drone"),
                name="November Drone 3",
                callsign="nov-d3",
                serial_number="TR-CD-0010",
                status=RobotStatus.OFFLINE,
                battery_percent=0,
                firmware_version="1.4.0",
                hardware_revision="Rev-B",
                is_autonomous=True,
                operator=people.get("manager2"),
                commissioned_at=now - timedelta(days=70),
                last_seen_at=now - timedelta(days=5),
                cap_slugs=["delivery"],
            ),
            # ── Community 3 — South Chapter ──
            dict(
                community=community["south"],
                model=_model("Sub-7 Crawler"),
                name="Sierra Underwater 1",
                callsign="sierra-uw1",
                serial_number="DS-S7-0001",
                status=RobotStatus.ACTIVE,
                battery_percent=84,
                firmware_version="1.1.0",
                hardware_revision="Rev-A",
                is_autonomous=True,
                operator=people.get("staff5"),
                commissioned_at=now - timedelta(days=50),
                last_seen_at=now - timedelta(minutes=25),
                cap_slugs=["underwater_nav", "inspection", "structural_analysis"],
            ),
            dict(
                community=community["south"],
                model=_model("Scout Rover"),
                name="Sierra Ground 2",
                callsign="sierra-g2",
                serial_number="TR-SR-0020",
                status=RobotStatus.PROVISIONING,
                battery_percent=0,
                firmware_version="",
                hardware_revision="Rev-C",
                is_autonomous=True,
                operator=None,
                commissioned_at=None,
                last_seen_at=None,
                cap_slugs=["mapping", "patrol"],
            ),
            dict(
                community=community["south"],
                model=_model("VisionBot 4S"),
                name="Sierra Vision 3",
                callsign="sierra-v3",
                serial_number="AML-V4-0001",
                status=RobotStatus.ACTIVE,
                battery_percent=55,
                firmware_version="2.1.0",
                hardware_revision="Rev-A",
                is_autonomous=True,
                operator=people.get("staff6"),
                commissioned_at=now - timedelta(days=30),
                last_seen_at=now - timedelta(minutes=7),
                cap_slugs=["inspection", "thermal_sensing", "hazard_detection"],
            ),
        ]

        robots = {}
        for spec in specs:
            cap_slugs = spec.pop("cap_slugs", [])
            callsign = spec["callsign"]
            robot, created = Robot.objects.get_or_create(
                community=spec["community"],
                callsign=callsign,
                defaults={k: v for k, v in spec.items() if k != "community" and k != "callsign"},
            )
            for slug in cap_slugs:
                cap = RobotCapability.objects.filter(slug=slug).first()
                if cap:
                    robot.capabilities.add(cap)
            robots[callsign] = robot
            self._ok(f"robot '{robot}'", created)
        return robots

    # ------------------------------------------------------------------ fleets

    def _seed_fleets(self, robots, people, community):
        now = timezone.now()

        fleet_specs = [
            dict(
                community=community["main"],
                name="Aerial Ops Fleet",
                slug="aerial-ops-fleet",
                description="All aerial drones and high-altitude sensor platforms for community-1.",
                manager=people.get("marek"),
                members=[
                    ("charlie-a1", "Lead Drone"),
                    ("delta-a1", "Wing Drone"),
                    ("hotel-s1", "Atmospheric Sensor"),
                ],
            ),
            dict(
                community=community["main"],
                name="Ground Operations",
                slug="ground-operations",
                description="Ground-based patrol, delivery, and warehouse robots for community-1.",
                manager=people.get("ewa"),
                members=[
                    ("alpha-1", "Scout Lead"),
                    ("bravo-1", "Scout Wing"),
                    ("foxtrot-arm1", "Warehouse Arm"),
                    ("echo-m1", "Marine Scout"),
                    ("golf-h1", "Humanoid Assist"),
                ],
            ),
            dict(
                community=community["north"],
                name="Northern Recon Group",
                slug="northern-recon-group",
                description="Reconnaissance and patrol fleet for the North Chapter.",
                manager=people.get("manager1"),
                members=[
                    ("nov-g1", "Scout"),
                    ("nov-ugv2", "Heavy Patrol"),
                    ("nov-d3", "Aerial Recon"),
                ],
            ),
            dict(
                community=community["south"],
                name="Southern Survey Fleet",
                slug="southern-survey-fleet",
                description="Survey, inspection, and subsurface exploration fleet for the South Chapter.",
                manager=people.get("staff5"),
                members=[
                    ("sierra-uw1", "Underwater Lead"),
                    ("sierra-g2", "Ground Support"),
                    ("sierra-v3", "Vision Overwatch"),
                ],
            ),
        ]

        fleets = {}
        for spec in fleet_specs:
            members = spec.pop("members", [])
            fleet, created = Fleet.objects.get_or_create(
                community=spec["community"],
                slug=spec["slug"],
                defaults={k: v for k, v in spec.items() if k != "community" and k != "slug"},
            )
            for callsign, role in members:
                robot = robots.get(callsign)
                if robot:
                    FleetMembership.objects.get_or_create(
                        fleet=fleet, robot=robot, defaults={"role": role, "joined_at": now - timedelta(days=30)},
                    )
            fleets[spec["slug"]] = fleet
            self._ok(f"fleet '{fleet.name}'", created)
        return fleets

    # ------------------------------------------------------------------ missions

    def _seed_missions(self, robots, fleets, people, community):
        now = timezone.now()

        def _fleet(slug):
            return fleets.get(slug)

        def _robot(cs):
            return robots.get(cs)

        mission_specs = [
            # ── Community 1 active missions ──
            dict(
                community=community["main"],
                fleet=_fleet("ground-operations"),
                title="Perimeter Survey Alpha-7",
                description="Full perimeter sweep of the northern compound using ground scouts.",
                status=MissionStatus.RUNNING,
                priority=3,
                requested_by=people.get("piotr"),
                supervisor=people.get("ewa"),
                started_at=now - timedelta(hours=2),
                scheduled_start_at=now - timedelta(hours=3),
                scheduled_end_at=now + timedelta(hours=1),
                key="perimeter-survey-alpha-7",
                assignments=[
                    ("alpha-1", "Lead Scout", AssignmentStatus.RUNNING),
                    ("bravo-1", "Support Scout", AssignmentStatus.RUNNING),
                ],
                waypoints=[
                    dict(order=1, label="Gate Alpha", instruction="Scan gate area.", latitude=Decimal("52.2297"), longitude=Decimal("21.0122"), hold_seconds=30),
                    dict(order=2, label="North Wall", instruction="Patrol north perimeter wall.", latitude=Decimal("52.2310"), longitude=Decimal("21.0145"), hold_seconds=0),
                    dict(order=3, label="Gate Beta", instruction="Final scan at east gate.", latitude=Decimal("52.2285"), longitude=Decimal("21.0170"), hold_seconds=30),
                ],
            ),
            dict(
                community=community["main"],
                fleet=_fleet("aerial-ops-fleet"),
                title="Package Delivery B7 — Urgent",
                description="Drone delivery of medical supplies to field station B7.",
                status=MissionStatus.RUNNING,
                priority=1,
                requested_by=people.get("ada"),
                supervisor=people.get("marek"),
                started_at=now - timedelta(minutes=45),
                scheduled_start_at=now - timedelta(hours=1),
                scheduled_end_at=now + timedelta(minutes=30),
                key="package-delivery-b7",
                assignments=[
                    ("delta-a1", "Primary Carrier", AssignmentStatus.RUNNING),
                ],
                waypoints=[
                    dict(order=1, label="Pickup Point", instruction="Collect payload from depot rooftop.", latitude=Decimal("52.2310"), longitude=Decimal("21.0100"), hold_seconds=60),
                    dict(order=2, label="Field Station B7", instruction="Deliver to marked landing pad.", latitude=Decimal("52.2050"), longitude=Decimal("20.9900"), hold_seconds=120),
                ],
            ),
            dict(
                community=community["main"],
                fleet=_fleet("ground-operations"),
                title="Warehouse Inventory Q1 Audit",
                description="Full stock count and barcode verification for Q1 audit.",
                status=MissionStatus.COMPLETED,
                priority=5,
                requested_by=people.get("founder"),
                supervisor=people.get("staff3"),
                started_at=now - timedelta(days=3, hours=6),
                completed_at=now - timedelta(days=3),
                scheduled_start_at=now - timedelta(days=4),
                scheduled_end_at=now - timedelta(days=3),
                key="warehouse-inventory-q1",
                assignments=[
                    ("foxtrot-arm1", "Inventory Scanner", AssignmentStatus.COMPLETED),
                ],
                waypoints=[],
                result={"items_scanned": 4821, "discrepancies": 3, "duration_minutes": 362},
            ),
            dict(
                community=community["main"],
                fleet=_fleet("aerial-ops-fleet"),
                title="Coastal Sensor Sweep — Week 7",
                description="Weekly atmospheric and surface water quality scan along the coastal strip.",
                status=MissionStatus.COMPLETED,
                priority=5,
                requested_by=people.get("ada"),
                supervisor=people.get("ada"),
                started_at=now - timedelta(days=7, hours=4),
                completed_at=now - timedelta(days=7, hours=1),
                scheduled_start_at=now - timedelta(days=8),
                scheduled_end_at=now - timedelta(days=7),
                key="coastal-sensor-sweep-w7",
                assignments=[
                    ("echo-m1", "Primary Sensor", AssignmentStatus.COMPLETED),
                ],
                waypoints=[
                    dict(order=1, label="Survey Start", instruction="Begin coastal transect.", latitude=Decimal("52.2400"), longitude=Decimal("21.0050"), hold_seconds=0),
                    dict(order=2, label="Midpoint Buoy A", instruction="Sample water quality.", latitude=Decimal("52.2350"), longitude=Decimal("20.9950"), hold_seconds=300),
                    dict(order=3, label="Survey End", instruction="Return to dock.", latitude=Decimal("52.2300"), longitude=Decimal("20.9850"), hold_seconds=0),
                ],
                result={"co2_ppm_avg": 412.3, "water_temp_c": 14.2, "turbidity_ntu": 8.1},
            ),
            dict(
                community=community["main"],
                fleet=_fleet("ground-operations"),
                title="Security Patrol Run #12",
                description="Routine dual-robot night-time security sweep of the facility.",
                status=MissionStatus.COMPLETED,
                priority=4,
                requested_by=people.get("piotr"),
                supervisor=people.get("ewa"),
                started_at=now - timedelta(days=2, hours=20),
                completed_at=now - timedelta(days=2, hours=18),
                scheduled_start_at=now - timedelta(days=3),
                scheduled_end_at=now - timedelta(days=2, hours=18),
                key="security-patrol-12",
                assignments=[
                    ("alpha-1", "Lead Scout", AssignmentStatus.COMPLETED),
                    ("bravo-1", "Rear Guard", AssignmentStatus.COMPLETED),
                ],
                waypoints=[
                    dict(order=1, label="Main Entrance", instruction="Check main gate.", latitude=Decimal("52.2290"), longitude=Decimal("21.0100"), hold_seconds=60),
                    dict(order=2, label="East Wing", instruction="Perimeter east.", latitude=Decimal("52.2280"), longitude=Decimal("21.0150"), hold_seconds=0),
                    dict(order=3, label="West Wing", instruction="Perimeter west.", latitude=Decimal("52.2295"), longitude=Decimal("21.0080"), hold_seconds=0),
                ],
                result={"incidents": 0, "coverage_pct": 100},
            ),
            dict(
                community=community["main"],
                fleet=_fleet("aerial-ops-fleet"),
                title="Dock Area Photographic Inspection",
                description="Aerial photo survey of dock 3 roof structures after storm damage report.",
                status=MissionStatus.PLANNED,
                priority=2,
                requested_by=people.get("ada"),
                supervisor=people.get("marek"),
                scheduled_start_at=now + timedelta(hours=4),
                scheduled_end_at=now + timedelta(hours=5, minutes=30),
                started_at=None,
                completed_at=None,
                key="dock-inspection-plan",
                assignments=[
                    ("charlie-a1", "Photo Lead", AssignmentStatus.QUEUED),
                ],
                waypoints=[
                    dict(order=1, label="Dock 3 North", instruction="Photograph north face.", latitude=Decimal("52.2320"), longitude=Decimal("21.0130"), hold_seconds=120),
                    dict(order=2, label="Dock 3 South", instruction="Photograph south face.", latitude=Decimal("52.2305"), longitude=Decimal("21.0128"), hold_seconds=120),
                ],
            ),
            dict(
                community=community["main"],
                fleet=_fleet("aerial-ops-fleet"),
                title="Emergency Flood Sensor Deployment",
                description="Deploy atmospheric sensor nodes to the eastern flood zone for real-time monitoring.",
                status=MissionStatus.DISPATCHED,
                priority=1,
                requested_by=people.get("piotr"),
                supervisor=people.get("piotr"),
                scheduled_start_at=now + timedelta(minutes=30),
                scheduled_end_at=now + timedelta(hours=2),
                started_at=None,
                completed_at=None,
                key="emergency-flood-sensor",
                assignments=[
                    ("charlie-a1", "Lead Carrier", AssignmentStatus.ACCEPTED),
                    ("hotel-s1", "Sensor Node", AssignmentStatus.ACCEPTED),
                ],
                waypoints=[
                    dict(order=1, label="Zone Alpha", instruction="Drop sensor node A.", latitude=Decimal("52.2150"), longitude=Decimal("21.0300"), hold_seconds=90),
                    dict(order=2, label="Zone Beta", instruction="Drop sensor node B.", latitude=Decimal("52.2100"), longitude=Decimal("21.0350"), hold_seconds=90),
                ],
            ),
            dict(
                community=community["main"],
                fleet=None,
                title="Titan-H1 Emergency Shutdown Response",
                description="Mission spawned after Golf's fault — remote shutdown and system diagnostic run.",
                status=MissionStatus.FAILED,
                priority=1,
                requested_by=people.get("piotr"),
                supervisor=people.get("piotr"),
                started_at=now - timedelta(hours=5, minutes=45),
                completed_at=now - timedelta(hours=5, minutes=10),
                scheduled_start_at=now - timedelta(hours=6),
                scheduled_end_at=now - timedelta(hours=5),
                key="titan-h1-fault-recovery",
                assignments=[
                    ("golf-h1", "Target Robot", AssignmentStatus.FAILED),
                ],
                waypoints=[],
                result={"error": "Motor drive fault — servo overload on left knee joint.", "steps_completed": 2, "steps_total": 8},
            ),
            # ── Community 2 missions ──
            dict(
                community=community["north"],
                fleet=_fleet("northern-recon-group"),
                title="Northern Border Recon — Sweep 4",
                description="Systematic recon sweep of northern compound border with thermal overlay.",
                status=MissionStatus.RUNNING,
                priority=3,
                requested_by=people.get("manager1"),
                supervisor=people.get("manager1"),
                started_at=now - timedelta(hours=1),
                scheduled_start_at=now - timedelta(hours=2),
                scheduled_end_at=now + timedelta(hours=2),
                key="north-border-recon-4",
                assignments=[
                    ("nov-g1", "Scout Lead", AssignmentStatus.RUNNING),
                ],
                waypoints=[
                    dict(order=1, label="Checkpoint 1", instruction="Begin sweep at NW corner.", latitude=Decimal("53.1320"), longitude=Decimal("18.0100"), hold_seconds=30),
                    dict(order=2, label="Checkpoint 2", instruction="Mid-north boundary.", latitude=Decimal("53.1350"), longitude=Decimal("18.0200"), hold_seconds=0),
                ],
            ),
            dict(
                community=community["north"],
                fleet=_fleet("northern-recon-group"),
                title="Trail Mapping — Lake Loop",
                description="Terrain mapping of the lake access trail for infrastructure planning.",
                status=MissionStatus.COMPLETED,
                priority=6,
                requested_by=people.get("manager2"),
                supervisor=people.get("manager1"),
                started_at=now - timedelta(days=5, hours=3),
                completed_at=now - timedelta(days=5, hours=1),
                scheduled_start_at=now - timedelta(days=6),
                scheduled_end_at=now - timedelta(days=5),
                key="trail-mapping-lake",
                assignments=[
                    ("nov-ugv2", "Terrain Mapper", AssignmentStatus.COMPLETED),
                ],
                waypoints=[
                    dict(order=1, label="Trail Head", instruction="Start mapping run.", latitude=Decimal("53.1200"), longitude=Decimal("18.0050"), hold_seconds=0),
                    dict(order=2, label="Lake Shore A", instruction="Map western shore.", latitude=Decimal("53.1180"), longitude=Decimal("17.9950"), hold_seconds=60),
                    dict(order=3, label="Trail End", instruction="Complete loop.", latitude=Decimal("53.1210"), longitude=Decimal("18.0030"), hold_seconds=0),
                ],
                result={"distance_km": 4.8, "elevation_gain_m": 32, "map_resolution_m": 0.5},
            ),
            dict(
                community=community["north"],
                fleet=_fleet("northern-recon-group"),
                title="Infrastructure Antenna Check — Nov-3",
                description="Fly-by visual inspection of the communications tower antennas.",
                status=MissionStatus.CANCELLED,
                priority=5,
                requested_by=people.get("manager2"),
                supervisor=None,
                scheduled_start_at=now - timedelta(days=4),
                scheduled_end_at=now - timedelta(days=4, hours=-3),
                started_at=None,
                completed_at=None,
                key="infra-antenna-check",
                assignments=[
                    ("nov-d3", "Recon Drone", AssignmentStatus.CANCELLED),
                ],
                waypoints=[
                    dict(order=1, label="Tower Base", instruction="Ascend to 80m.", latitude=Decimal("53.1300"), longitude=Decimal("18.0150"), hold_seconds=0),
                    dict(order=2, label="Antenna Level", instruction="Circle antenna array.", latitude=Decimal("53.1300"), longitude=Decimal("18.0150"), altitude_m=Decimal("80.000"), hold_seconds=300),
                ],
            ),
            # ── Community 3 missions ──
            dict(
                community=community["south"],
                fleet=_fleet("southern-survey-fleet"),
                title="Pipeline Integrity Survey — Sector 3",
                description="Sub-surface pipeline inspection using acoustic and visual sensors.",
                status=MissionStatus.RUNNING,
                priority=2,
                requested_by=people.get("staff5"),
                supervisor=people.get("staff5"),
                started_at=now - timedelta(hours=3),
                scheduled_start_at=now - timedelta(hours=4),
                scheduled_end_at=now + timedelta(hours=1),
                key="pipeline-survey-s3",
                assignments=[
                    ("sierra-uw1", "Crawler Lead", AssignmentStatus.RUNNING),
                ],
                waypoints=[
                    dict(order=1, label="Entry Lock", instruction="Descend to pipe level.", latitude=Decimal("50.0640"), longitude=Decimal("19.9450"), hold_seconds=60),
                    dict(order=2, label="Sector 3 Mid", instruction="Deploy acoustic sensor.", latitude=Decimal("50.0610"), longitude=Decimal("19.9500"), hold_seconds=180),
                    dict(order=3, label="Sector 3 End", instruction="Return to surface.", latitude=Decimal("50.0580"), longitude=Decimal("19.9550"), hold_seconds=60),
                ],
            ),
            dict(
                community=community["south"],
                fleet=_fleet("southern-survey-fleet"),
                title="South Perimeter Vision Patrol",
                description="Scheduled vision-guided patrol of southern boundary with anomaly detection.",
                status=MissionStatus.PLANNED,
                priority=4,
                requested_by=people.get("staff6"),
                supervisor=people.get("staff5"),
                scheduled_start_at=now + timedelta(hours=6),
                scheduled_end_at=now + timedelta(hours=7, minutes=30),
                started_at=None,
                completed_at=None,
                key="south-vision-patrol",
                assignments=[
                    ("sierra-v3", "Vision Patrol", AssignmentStatus.QUEUED),
                ],
                waypoints=[
                    dict(order=1, label="South Gate", instruction="Begin patrol.", latitude=Decimal("50.0600"), longitude=Decimal("19.9400"), hold_seconds=30),
                    dict(order=2, label="South East Corner", instruction="Thermal scan.", latitude=Decimal("50.0580"), longitude=Decimal("19.9480"), hold_seconds=60),
                ],
            ),
        ]

        # Spread missions across the last 30 days for the "missions over time" chart
        mission_age_days = {
            "warehouse-inventory-q1":    28,
            "coastal-sensor-sweep-w7":   24,
            "trail-mapping-lake":        20,
            "security-patrol-12":        16,
            "titan-h1-fault-recovery":   12,
            "infra-antenna-check":        9,
            "north-border-recon-4":       7,
            "pipeline-survey-s3":         5,
            "perimeter-survey-alpha-7":   3,
            "package-delivery-b7":        2,
            "dock-inspection-plan":       1,
            "emergency-flood-sensor":     0,
            "south-vision-patrol":        0,
        }

        missions = {}
        for spec in mission_specs:
            key = spec.pop("key")
            assignments_data = spec.pop("assignments", [])
            waypoints_data = spec.pop("waypoints", [])

            if RobotMission.objects.filter(community=spec["community"], title=spec["title"]).exists():
                mission = RobotMission.objects.get(community=spec["community"], title=spec["title"])
                self._ok(f"mission '{mission.title}'", False)
            else:
                mission = RobotMission.objects.create(**spec)
                days_ago = mission_age_days.get(key, 0)
                if days_ago:
                    RobotMission.objects.filter(pk=mission.pk).update(
                        created_at=now - timedelta(days=days_ago)
                    )
                self._ok(f"mission '{mission.title}'", True)

                for a_callsign, role, status in assignments_data:
                    robot = robots.get(a_callsign)
                    if robot:
                        RobotAssignment.objects.get_or_create(
                            mission=mission,
                            robot=robot,
                            defaults={"role": role, "status": status},
                        )

                for wp_spec in waypoints_data:
                    altitude = wp_spec.pop("altitude_m", None)
                    MissionWaypoint.objects.get_or_create(
                        mission=mission,
                        order=wp_spec["order"],
                        defaults={**wp_spec, "altitude_m": altitude},
                    )

            missions[key] = mission
        return missions

    # ------------------------------------------------------------------ telemetry

    def _seed_telemetry(self, robots, missions):
        now = timezone.now()

        active_robots = [
            ("alpha-1",    52.2297, 21.0122),
            ("bravo-1",    52.2310, 21.0145),
            ("charlie-a1", 52.2350, 21.0200),
            ("delta-a1",   52.2050, 20.9900),
            ("echo-m1",    52.2400, 21.0050),
            ("nov-g1",     53.1320, 18.0100),
            ("sierra-uw1", 50.0640, 19.9450),
            ("sierra-v3",  50.0580, 19.9480),
        ]

        total = 0
        for callsign, base_lat, base_lon in active_robots:
            robot = robots.get(callsign)
            if not robot:
                continue
            if RobotTelemetry.objects.filter(robot=robot).exists():
                continue

            for day_offset in range(30, 0, -1):
                for hour_offset in [6, 14, 20]:
                    ts = now - timedelta(days=day_offset, hours=hour_offset)
                    jitter_lat = random.uniform(-0.002, 0.002)
                    jitter_lon = random.uniform(-0.002, 0.002)
                    battery = max(10, min(100, 100 - (day_offset % 15) * 5 + random.randint(-5, 5)))
                    speed = Decimal(str(round(random.uniform(0.5, 3.0), 3)))
                    RobotTelemetry.objects.create(
                        robot=robot,
                        recorded_at=ts,
                        latitude=Decimal(str(round(base_lat + jitter_lat, 6))),
                        longitude=Decimal(str(round(base_lon + jitter_lon, 6))),
                        speed_mps=speed,
                        heading_degrees=Decimal(str(round(random.uniform(0, 360), 2))),
                        battery_percent=battery,
                        battery_voltage=Decimal(str(round(24.0 + battery * 0.02, 3))),
                        temperature_c=Decimal(str(round(random.uniform(28.0, 55.0), 3))),
                        signal_strength=random.randint(-80, -40),
                        cpu_percent=Decimal(str(round(random.uniform(10.0, 75.0), 2))),
                        memory_percent=Decimal(str(round(random.uniform(20.0, 65.0), 2))),
                    )
                    total += 1

        self.stdout.write(f"  + {total} telemetry readings seeded.")

    # ------------------------------------------------------------------ events

    def _seed_events(self, robots, missions, people):
        now = timezone.now()

        event_templates = [
            # (callsign, severity, event_type, message)
            ("alpha-1",    EventSeverity.INFO,     "mission_started",       "Perimeter Survey Alpha-7 started. All systems nominal."),
            ("alpha-1",    EventSeverity.INFO,     "waypoint_reached",      "Waypoint 1 (Gate Alpha) reached and scanned."),
            ("alpha-1",    EventSeverity.WARNING,  "low_battery_warning",   "Battery at 25%. Returning to dock after current waypoint."),
            ("alpha-1",    EventSeverity.INFO,     "waypoint_reached",      "Waypoint 2 (North Wall) reached. No anomalies detected."),
            ("bravo-1",    EventSeverity.INFO,     "patrol_complete",       "Security Patrol #12 completed. 0 incidents logged."),
            ("bravo-1",    EventSeverity.INFO,     "status_change",         "Status changed to IDLE after mission completion."),
            ("charlie-a1", EventSeverity.INFO,     "takeoff_confirmed",     "Drone Charlie lifted off from aerial pad, heading to Dock 3."),
            ("charlie-a1", EventSeverity.WARNING,  "wind_speed_elevated",   "Wind speed 18 km/h — approaching operational limit. Reducing speed."),
            ("charlie-a1", EventSeverity.INFO,     "delivery_confirmed",    "Package delivered to field station B7. Signature captured."),
            ("delta-a1",   EventSeverity.INFO,     "mission_started",       "Delivery B7 started. Payload weight confirmed: 1.8 kg."),
            ("delta-a1",   EventSeverity.WARNING,  "gps_signal_degraded",   "GPS accuracy degraded over industrial zone. Switching to dead-reckoning."),
            ("delta-a1",   EventSeverity.ERROR,    "obstacle_detected",     "Obstacle detected on descent path at Waypoint 2. Rerouting."),
            ("echo-m1",    EventSeverity.INFO,     "surface_entry",         "Echo Marine entered water at dock ramp. Thrusters nominal."),
            ("echo-m1",    EventSeverity.INFO,     "sensor_reading",        "Water temperature 14.2°C, turbidity 8.1 NTU — within normal range."),
            ("echo-m1",    EventSeverity.INFO,     "charging_started",      "Echo Marine docked and charging session initiated."),
            ("foxtrot-arm1", EventSeverity.INFO,   "scan_complete",         "Q1 inventory scan complete. 4821 items verified."),
            ("foxtrot-arm1", EventSeverity.WARNING,"calibration_required",  "Position encoder drift detected. Calibration recommended before next cycle."),
            ("golf-h1",    EventSeverity.ERROR,    "servo_fault",           "Left knee servo overload — current draw 14.2A (limit 8A). Halting movement."),
            ("golf-h1",    EventSeverity.CRITICAL, "emergency_stop",        "Emergency stop triggered after servo fault. Robot is immobilized and faulted."),
            ("golf-h1",    EventSeverity.ERROR,    "battery_drain_high",    "Battery draining at 3× nominal rate. Likely fault-state power draw."),
            ("hotel-s1",   EventSeverity.WARNING,  "sensor_calibration_due","CO2 sensor calibration overdue by 7 days. Readings may drift."),
            ("hotel-s1",   EventSeverity.ERROR,    "hardware_error",        "IMU initialization failed on boot. Robot placed in maintenance mode."),
            ("nov-g1",     EventSeverity.INFO,     "patrol_started",        "Northern Border Recon sweep 4 initiated."),
            ("nov-g1",     EventSeverity.INFO,     "checkpoint_passed",     "Checkpoint 1 (NW corner) cleared. No intrusion signals."),
            ("nov-ugv2",   EventSeverity.INFO,     "mapping_complete",      "Lake Loop trail mapped. 4.8 km at 0.5m resolution."),
            ("nov-d3",     EventSeverity.WARNING,  "connection_lost",       "Telemetry link lost. Last known position: [53.1302, 18.0148]."),
            ("nov-d3",     EventSeverity.ERROR,    "mission_cancelled",     "Mission cancelled due to persistent comms failure. Manual recovery needed."),
            ("nov-d3",     EventSeverity.CRITICAL, "system_offline",        "Robot went offline. No heartbeat for >5 minutes. Flag for physical inspection."),
            ("sierra-uw1", EventSeverity.INFO,     "dive_started",          "Sierra Underwater 1 submerged to pipe access depth."),
            ("sierra-uw1", EventSeverity.INFO,     "acoustic_ping_ok",      "Acoustic ping returned normal. Pipe integrity nominal at Sector 3 Mid."),
            ("sierra-v3",  EventSeverity.INFO,     "commissioned",          "Sierra Vision 3 commissioned and system check passed."),
            ("sierra-v3",  EventSeverity.INFO,     "thermal_anomaly",       "Thermal anomaly detected at south gate — possible warm exhaust vent. Logged."),
            ("alpha-1",    EventSeverity.INFO,     "firmware_updated",      "Firmware updated to v2.2.0. Reboot successful. All diagnostics green."),
            ("charlie-a1", EventSeverity.INFO,     "firmware_updated",      "Firmware updated to v1.5.0. Flight controller calibration successful."),
        ]

        total = 0
        for i, (callsign, severity, etype, msg) in enumerate(event_templates):
            robot = robots.get(callsign)
            if not robot:
                continue
            hours_ago = max(0, (len(event_templates) - i) * 4 - random.randint(0, 6))
            ts = now - timedelta(hours=hours_ago)

            if not RobotEvent.objects.filter(robot=robot, event_type=etype).exists():
                RobotEvent.objects.create(
                    robot=robot,
                    severity=severity,
                    event_type=etype,
                    message=msg,
                    occurred_at=ts,
                )
                total += 1

        # Add some extra events to fill the 7-day chart more visually
        bulk_specs = [
            ("alpha-1",    EventSeverity.INFO,    "heartbeat",             "Periodic heartbeat OK."),
            ("bravo-1",    EventSeverity.INFO,    "heartbeat",             "Periodic heartbeat OK."),
            ("charlie-a1", EventSeverity.INFO,    "heartbeat",             "Periodic heartbeat OK."),
            ("nov-g1",     EventSeverity.INFO,    "heartbeat",             "Periodic heartbeat OK."),
            ("sierra-v3",  EventSeverity.INFO,    "heartbeat",             "Periodic heartbeat OK."),
            ("alpha-1",    EventSeverity.WARNING, "battery_low",           "Battery below 30%."),
            ("delta-a1",   EventSeverity.WARNING, "return_to_base",        "Auto return triggered: battery at 35%."),
            ("sierra-uw1", EventSeverity.WARNING, "tether_tension_high",   "Tether tension elevated — check deployment spool."),
            ("golf-h1",    EventSeverity.ERROR,   "actuator_stall",        "Actuator stall on right shoulder joint."),
            ("nov-d3",     EventSeverity.ERROR,   "comms_timeout",         "Communication timeout — 3rd consecutive failure."),
            ("hotel-s1",   EventSeverity.WARNING, "memory_high",           "System memory at 87% — process leak suspected."),
            ("foxtrot-arm1", EventSeverity.INFO,  "cycle_complete",        "Arm cycle 1042 completed without errors."),
        ]

        for day in range(7):
            for callsign, severity, etype, msg in bulk_specs:
                robot = robots.get(callsign)
                if not robot:
                    continue
                ts = now - timedelta(days=day, hours=random.randint(0, 20))
                unique_etype = f"{etype}_d{day}"
                if not RobotEvent.objects.filter(robot=robot, event_type=unique_etype).exists():
                    RobotEvent.objects.create(
                        robot=robot,
                        severity=severity,
                        event_type=unique_etype,
                        message=msg,
                        occurred_at=ts,
                    )
                    total += 1

        self.stdout.write(f"  + {total} robot events seeded.")

    # ------------------------------------------------------------------ maintenance

    def _seed_maintenance(self, robots, people):
        now = timezone.now()

        specs = [
            dict(
                robot=robots.get("golf-h1"),
                title="Left Knee Servo Overload — Motor Drive Fault",
                description=(
                    "Left knee servo repeatedly drawing 14A against an 8A limit. "
                    "Suspect failed motor driver IC. Robot is immobilized in faulted state."
                ),
                status=MaintenanceStatus.IN_PROGRESS,
                severity=EventSeverity.CRITICAL,
                reported_by=people.get("piotr"),
                assigned_to=people.get("staff3"),
                opened_at=now - timedelta(hours=6),
                due_at=now + timedelta(hours=18),
                actions=[
                    dict(action_type="initial_triage", notes="Confirmed motor driver overtemp. Ordered replacement IC board. ETA 16h.", performed_by=people.get("piotr"), performed_at=now - timedelta(hours=5)),
                    dict(action_type="part_ordered", notes="Part #AM-MD-4420 ordered from AstroMech Labs.", performed_by=people.get("staff3"), performed_at=now - timedelta(hours=4)),
                ],
            ),
            dict(
                robot=robots.get("hotel-s1"),
                title="IMU Initialization Failure — Sensor Calibration Required",
                description=(
                    "IMU fails to initialize on every boot since firmware 2.0.0 rollout. "
                    "CO2 sensor also flagged overdue calibration. Robot placed in maintenance."
                ),
                status=MaintenanceStatus.TRIAGED,
                severity=EventSeverity.ERROR,
                reported_by=people.get("staff4"),
                assigned_to=people.get("staff4"),
                opened_at=now - timedelta(days=2),
                due_at=now + timedelta(days=2),
                actions=[
                    dict(action_type="log_review", notes="IMU error trace: I2C timeout on address 0x68. Possible soldering fault from last PCB replacement.", performed_by=people.get("staff4"), performed_at=now - timedelta(days=1, hours=20)),
                ],
            ),
            dict(
                robot=robots.get("nov-d3"),
                title="Antenna Assembly Replacement — Comms Failure",
                description=(
                    "November Drone 3 suffered persistent telemetry loss. "
                    "Antenna connector cracked, likely from last hard landing. "
                    "Awaiting replacement antenna from supplier."
                ),
                status=MaintenanceStatus.WAITING_PARTS,
                severity=EventSeverity.ERROR,
                reported_by=people.get("manager2"),
                assigned_to=people.get("staff3"),
                opened_at=now - timedelta(days=5),
                due_at=now + timedelta(days=3),
                actions=[
                    dict(action_type="visual_inspection", notes="Confirmed cracked SMA connector on 2.4 GHz antenna port. RF signal absent.", performed_by=people.get("manager2"), performed_at=now - timedelta(days=4, hours=18)),
                    dict(action_type="part_ordered", notes="Ordered replacement coaxial assembly from drone parts supplier. Lead time 5–7 days.", performed_by=people.get("staff3"), performed_at=now - timedelta(days=4)),
                ],
            ),
            dict(
                robot=robots.get("echo-m1"),
                title="Starboard Thruster Cavitation — Post-Coastal Sweep",
                description=(
                    "Unusual vibration reported during coastal sweep Week 7 mission. "
                    "Thruster inspection revealed partial debris obstruction. "
                    "Cleared and re-tested — resolved."
                ),
                status=MaintenanceStatus.RESOLVED,
                severity=EventSeverity.WARNING,
                reported_by=people.get("ada"),
                assigned_to=people.get("staff5"),
                opened_at=now - timedelta(days=7),
                due_at=now - timedelta(days=5),
                resolved_at=now - timedelta(days=6, hours=8),
                actions=[
                    dict(action_type="inspection", notes="Found 3 pebbles and a plastic fragment in thruster housing. Cleared.", performed_by=people.get("staff5"), performed_at=now - timedelta(days=7, hours=2)),
                    dict(action_type="test_run", notes="Post-clearance test run at 80% thrust. Vibration eliminated. Robot cleared for service.", performed_by=people.get("staff5"), performed_at=now - timedelta(days=6, hours=8)),
                ],
            ),
            dict(
                robot=robots.get("alpha-1"),
                title="Routine 6-Month Chassis and Wheel Inspection",
                description=(
                    "Scheduled preventive maintenance inspection at 180-day mark. "
                    "All items checked and signed off. No defects found."
                ),
                status=MaintenanceStatus.CLOSED,
                severity=EventSeverity.INFO,
                reported_by=people.get("ewa"),
                assigned_to=people.get("staff3"),
                opened_at=now - timedelta(days=14),
                due_at=now - timedelta(days=10),
                resolved_at=now - timedelta(days=12),
                closed_at=now - timedelta(days=11),
                actions=[
                    dict(action_type="chassis_inspection", notes="Chassis frame: no cracks. All fasteners torqued. Wheel tread measured — 6.2mm remaining, within spec.", performed_by=people.get("staff3"), performed_at=now - timedelta(days=12, hours=4)),
                    dict(action_type="lidar_calibration", notes="Lidar horizontal calibration ran. Max deviation 0.02°. Within tolerance.", performed_by=people.get("staff3"), performed_at=now - timedelta(days=12, hours=3)),
                    dict(action_type="sign_off", notes="All checks passed. Robot returned to active service.", performed_by=people.get("ewa"), performed_at=now - timedelta(days=11)),
                ],
            ),
            dict(
                robot=robots.get("foxtrot-arm1"),
                title="Position Encoder Drift — Calibration Warning",
                description="Encoder drift flagged by self-diagnostic during Q1 audit mission. Scheduled calibration required.",
                status=MaintenanceStatus.OPEN,
                severity=EventSeverity.WARNING,
                reported_by=people.get("staff3"),
                assigned_to=None,
                opened_at=now - timedelta(hours=1),
                due_at=now + timedelta(days=7),
                actions=[],
            ),
        ]

        for spec in specs:
            actions_data = spec.pop("actions", [])
            robot = spec.get("robot")
            if not robot:
                continue
            if MaintenanceTicket.objects.filter(robot=robot, title=spec["title"]).exists():
                self._ok(f"maintenance ticket '{spec['title'][:50]}'", False)
                continue
            ticket = MaintenanceTicket.objects.create(**spec)
            for action_spec in actions_data:
                MaintenanceAction.objects.create(ticket=ticket, **action_spec)
            self._ok(f"maintenance ticket '{ticket.title[:50]}'", True)

    # ------------------------------------------------------------------ components

    def _seed_components(self, robots):
        now = timezone.now()

        component_specs = [
            ("alpha-1",    "Primary LiDAR",       "lidar",          "TR-LDR-2210", 8760,  Decimal("1842.50")),
            ("alpha-1",    "Motor Controller PCB", "motor_controller","TR-MCB-0041", 6000,  Decimal("412.00")),
            ("alpha-1",    "GPS/GNSS Module",      "gps_module",     "TR-GPS-0011", 17520, Decimal("180.00")),
            ("charlie-a1", "Front-Right Rotor",    "rotor",          "TR-ROT-0210", 500,   Decimal("112.30")),
            ("charlie-a1", "Rear-Left Rotor",      "rotor",          "TR-ROT-0211", 500,   Decimal("108.90")),
            ("charlie-a1", "Flight Controller",    "flight_controller","TR-FC-0080", 10000, Decimal("340.20")),
            ("golf-h1",    "Left Knee Servo",      "servo_actuator", "AML-SV-K001", 2000,  Decimal("780.00")),
            ("golf-h1",    "Right Shoulder Servo", "servo_actuator", "AML-SV-S002", 2000,  Decimal("780.00")),
            ("golf-h1",    "Battery Pack A",       "battery",        "AML-BP-0010", 3000,  Decimal("95.00")),
            ("sierra-uw1", "Acoustic Sensor Array","acoustic_sensor","DS-AS-0021",  5000,  Decimal("2800.00")),
            ("sierra-uw1", "Tether Spool Motor",   "motor",          "DS-TM-0003",  8000,  Decimal("450.00")),
            ("echo-m1",    "Starboard Thruster",   "thruster",       "HMS-TH-0022", 3000,  Decimal("620.00")),
            ("echo-m1",    "Water Quality Sensor", "environmental",  "HMS-WQS-0007",8760,  Decimal("1100.00")),
        ]

        total = 0
        for callsign, name, ctype, serial, lifetime, runtime in component_specs:
            robot = robots.get(callsign)
            if not robot:
                continue
            comp, created = RobotComponent.objects.get_or_create(
                robot=robot,
                serial_number=serial,
                defaults={
                    "name": name,
                    "component_type": ctype,
                    "installed_at": now - timedelta(days=90),
                    "expected_lifetime_hours": lifetime,
                    "runtime_hours": runtime,
                },
            )
            if created:
                # Seed a few component readings
                for day in [7, 3, 1]:
                    ComponentReading.objects.create(
                        component=comp,
                        recorded_at=now - timedelta(days=day),
                        metric="runtime_hours",
                        value=runtime - Decimal(str(day * 8)),
                        unit="h",
                    )
                total += 1

        self.stdout.write(f"  + {total} robot components seeded.")

    # ------------------------------------------------------------------ firmware

    def _seed_firmware(self, models, robots, people):
        now = timezone.now()

        firmware_data = [
            ("Scout Rover",   "2.0.0", "Stability improvements for rough terrain navigation.",     False, now - timedelta(days=200)),
            ("Scout Rover",   "2.1.0", "Added swarm coordination protocol v1.",                    False, now - timedelta(days=120)),
            ("Scout Rover",   "2.2.0", "LiDAR driver update — 25% faster point cloud processing.", True,  now - timedelta(days=45)),
            ("Courier Drone", "1.3.0", "Initial stable release.",                                   False, now - timedelta(days=300)),
            ("Courier Drone", "1.4.0", "Wind compensation algorithm v2.",                           False, now - timedelta(days=150)),
            ("Courier Drone", "1.5.0", "Obstacle avoidance rerouting engine.",                      True,  now - timedelta(days=60)),
            ("Titan-H1",      "1.1.0", "Balance controller improvements — standing stability.",     False, now - timedelta(days=90)),
            ("Titan-H1",      "1.2.0", "Servo protection logic — current limiting per joint.",      True,  now - timedelta(days=30)),
            ("AirSense Node", "1.5.0", "CO2 sensor driver rewrite.",                                False, now - timedelta(days=180)),
            ("AirSense Node", "2.0.0", "Multi-sensor fusion for air quality index.",                True,  now - timedelta(days=20)),
        ]

        installs = [
            ("alpha-1",    "Scout Rover",   "2.2.0", people.get("staff3")),
            ("bravo-1",    "Scout Rover",   "2.2.0", people.get("staff3")),
            ("charlie-a1", "Courier Drone", "1.5.0", people.get("marek")),
            ("delta-a1",   "Courier Drone", "1.5.0", people.get("marek")),
            ("golf-h1",    "Titan-H1",      "1.2.0", people.get("piotr")),
            ("hotel-s1",   "AirSense Node", "2.0.0", people.get("staff4")),
        ]

        fw_map = {}
        for model_name, version, notes, is_required, released_at in firmware_data:
            model = models.get(model_name)
            if not model:
                continue
            release, created = FirmwareRelease.objects.get_or_create(
                model=model,
                version=version,
                defaults={"release_notes": notes, "is_required": is_required, "released_at": released_at},
            )
            fw_map[(model_name, version)] = release
            self._ok(f"firmware {model_name} v{version}", created)

        for callsign, model_name, version, installed_by in installs:
            robot = robots.get(callsign)
            release = fw_map.get((model_name, version))
            if not robot or not release:
                continue
            _, created = FirmwareInstallation.objects.get_or_create(
                robot=robot,
                release=release,
                defaults={
                    "installed_by": installed_by,
                    "started_at": now - timedelta(days=44, hours=2),
                    "completed_at": now - timedelta(days=44, hours=1, minutes=50),
                    "success": True,
                    "log": f"Firmware {version} installed successfully. Reboot completed in 48s.",
                },
            )
            self._ok(f"firmware install {callsign} → v{version}", created)

    # ------------------------------------------------------------------ charging sessions

    def _seed_charging_sessions(self, robots, docks):
        now = timezone.now()

        hangar_a    = docks.get("main-hangar-dock-a")
        aerial_bay  = docks.get("aerial-pad-charging-bay")

        session_specs = [
            dict(robot=robots.get("echo-m1"),    dock=hangar_a,   started_at=now - timedelta(hours=2),           ended_at=None,                               start_battery_percent=22, end_battery_percent=None,   energy_kwh=None),
            dict(robot=robots.get("bravo-1"),    dock=hangar_a,   started_at=now - timedelta(days=1, hours=14),  ended_at=now - timedelta(days=1, hours=11),  start_battery_percent=31, end_battery_percent=95,     energy_kwh=Decimal("0.7600")),
            dict(robot=robots.get("delta-a1"),   dock=aerial_bay, started_at=now - timedelta(days=3, hours=8),   ended_at=now - timedelta(days=3, hours=7),   start_battery_percent=18, end_battery_percent=88,     energy_kwh=Decimal("0.1540")),
            dict(robot=robots.get("charlie-a1"), dock=aerial_bay, started_at=now - timedelta(days=2, hours=20),  ended_at=now - timedelta(days=2, hours=19),  start_battery_percent=22, end_battery_percent=100,    energy_kwh=Decimal("0.1716")),
            dict(robot=robots.get("alpha-1"),    dock=hangar_a,   started_at=now - timedelta(days=4, hours=22),  ended_at=now - timedelta(days=4, hours=18),  start_battery_percent=15, end_battery_percent=100,    energy_kwh=Decimal("1.2480")),
        ]

        total = 0
        for spec in session_specs:
            if not spec["robot"]:
                continue
            if ChargingSession.objects.filter(robot=spec["robot"], started_at__date=spec["started_at"].date()).exists():
                continue
            ChargingSession.objects.create(**spec)
            total += 1

        self.stdout.write(f"  + {total} charging sessions seeded.")

    # ------------------------------------------------------------------ commands

    def _seed_commands(self, robots, missions, people):
        now = timezone.now()

        command_specs = [
            dict(robot=robots.get("alpha-1"),    command_type="goto_waypoint",    payload={"lat": 52.2297, "lon": 21.0122, "speed_mps": 2.0},        issued_by=people.get("ewa"),    issued_at=now - timedelta(hours=2, minutes=5),  sent_at=now - timedelta(hours=2),         acknowledged_at=now - timedelta(hours=1, minutes=58), completed_at=now - timedelta(hours=1, minutes=45), success=True),
            dict(robot=robots.get("alpha-1"),    command_type="goto_waypoint",    payload={"lat": 52.2310, "lon": 21.0145, "speed_mps": 2.0},        issued_by=people.get("ewa"),    issued_at=now - timedelta(hours=1, minutes=40), sent_at=now - timedelta(hours=1, minutes=38), acknowledged_at=now - timedelta(hours=1, minutes=37), completed_at=None,                                 success=None),
            dict(robot=robots.get("charlie-a1"), command_type="takeoff",          payload={"altitude_m": 30, "mode": "auto"},                         issued_by=people.get("marek"),  issued_at=now - timedelta(minutes=50),          sent_at=now - timedelta(minutes=49),      acknowledged_at=now - timedelta(minutes=48),          completed_at=now - timedelta(minutes=47),           success=True),
            dict(robot=robots.get("charlie-a1"), command_type="goto_waypoint",    payload={"lat": 52.2310, "lon": 21.0100, "altitude_m": 30},         issued_by=people.get("marek"),  issued_at=now - timedelta(minutes=46),          sent_at=now - timedelta(minutes=45),      acknowledged_at=now - timedelta(minutes=44),          completed_at=None,                                 success=None),
            dict(robot=robots.get("delta-a1"),   command_type="return_to_base",   payload={"reason": "low_battery", "priority": "normal"},            issued_by=None,                 issued_at=now - timedelta(minutes=12),          sent_at=now - timedelta(minutes=11),      acknowledged_at=now - timedelta(minutes=10),          completed_at=None,                                 success=None),
            dict(robot=robots.get("golf-h1"),    command_type="emergency_stop",   payload={"reason": "servo_overload", "joint": "left_knee"},         issued_by=people.get("piotr"),  issued_at=now - timedelta(hours=5, minutes=58), sent_at=now - timedelta(hours=5, minutes=57), acknowledged_at=now - timedelta(hours=5, minutes=56), completed_at=now - timedelta(hours=5, minutes=56), success=True),
            dict(robot=robots.get("golf-h1"),    command_type="run_diagnostics",  payload={"subsystems": ["motors", "power", "comms"]},               issued_by=people.get("piotr"),  issued_at=now - timedelta(hours=5, minutes=30), sent_at=now - timedelta(hours=5, minutes=29), acknowledged_at=None,                                 completed_at=None,                                 success=False, error_message="Robot in faulted state — diagnostics command rejected."),
            dict(robot=robots.get("foxtrot-arm1"), command_type="run_calibration", payload={"target": "position_encoder", "axis": "all"},             issued_by=people.get("staff3"), issued_at=now - timedelta(hours=1),             sent_at=None,                             acknowledged_at=None,                                 completed_at=None,                                 success=None),
            dict(robot=robots.get("nov-g1"),     command_type="set_patrol_mode",  payload={"pattern": "sweep", "interval_s": 3600},                  issued_by=people.get("manager1"), issued_at=now - timedelta(hours=1, minutes=5), sent_at=now - timedelta(hours=1, minutes=4), acknowledged_at=now - timedelta(hours=1, minutes=3), completed_at=now - timedelta(hours=1, minutes=3), success=True),
            dict(robot=robots.get("sierra-uw1"), command_type="descend",          payload={"depth_m": 8, "speed_ms": 0.5},                            issued_by=people.get("staff5"), issued_at=now - timedelta(hours=3, minutes=5),  sent_at=now - timedelta(hours=3, minutes=4), acknowledged_at=now - timedelta(hours=3, minutes=3), completed_at=now - timedelta(hours=2, minutes=50), success=True),
            dict(robot=robots.get("sierra-uw1"), command_type="deploy_sensor",    payload={"sensor_id": "acoustic_01", "position": "sector_3_mid"}, issued_by=people.get("staff5"), issued_at=now - timedelta(hours=2, minutes=30), sent_at=now - timedelta(hours=2, minutes=29), acknowledged_at=now - timedelta(hours=2, minutes=28), completed_at=None,                                success=None),
        ]

        total = 0
        for spec in command_specs:
            if not spec["robot"]:
                continue
            if RobotCommand.objects.filter(robot=spec["robot"], command_type=spec["command_type"], issued_at__date=spec["issued_at"].date()).exists():
                continue
            RobotCommand.objects.create(**spec)
            total += 1

        self.stdout.write(f"  + {total} robot commands seeded.")
