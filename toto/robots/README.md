# toto.robots

Robot fleet management app for toto.

This package provides a complete Django app for manually managing robot fleets today, while leaving the data ingestion/control layer for later. It includes:

- robot model registry
- robot capability registry
- concrete robot records
- fleets and memberships
- missions, waypoints, and robot assignments
- telemetry snapshots
- event audit log
- charging docks and charging sessions
- components and component readings
- maintenance tickets and actions
- firmware releases and installations
- command queue/audit records
- Django admin registrations
- manual CRUD/forms/views
- OpenStreetMap/Leaflet operations map with robot markers, dock markers, mission routes, and waypoint fallback routes
- basic Celery task placeholders
- seed command for default capabilities and demo models

---

## Install

Copy the `robots/` folder into your project as:

```text
toto/robots/
```

Add the app:

```python
INSTALLED_APPS = [
    ...,
    "toto.robots",
]
```

Include URLs:

```python
path("robots/", include("toto.robots.urls")),
```

Create migrations:

```bash
python manage.py makemigrations robots
python manage.py migrate
```

Seed default capabilities:

```bash
python manage.py ingress_robots
python manage.py ingress_robots --full
```

---

## Manual-first workflow

This app intentionally does not assume automatic telemetry or command feeds yet.

For now you can:

1. Create robot models.
2. Create capabilities.
3. Register robots manually.
4. Create fleets.
5. Create missions.
6. Add waypoints or link a `locations.Route`.
7. Assign robots to missions.
8. Manually queue commands.
9. Manually record telemetry snapshots/events.
10. Use the map to view robot markers and mission routes.

Later, hardware connectors can write to the same models through `services.py`.

---

## Map behavior

The main map is available at:

```text
/robots/map/
```

It uses Leaflet with OpenStreetMap tiles.

It displays:

- robot markers from `Robot.current_location` or `Robot.home_base`
- charging dock markers from `ChargingDock.location`
- mission route geometry from `RobotMission.route.geometry`
- mission origin/destination markers
- mission waypoint markers
- dashed waypoint routes if no linked route geometry exists

The map data is also exposed as JSON:

```text
/robots/api/map/
```

---

## Dependencies

The app is designed for your existing toto architecture and references these apps by string FK:

- `socialhub.Community`
- `people.Person`
- `locations.Address`
- `locations.Route`
- `inventory.RealWorldObject`
- `assets.Asset`
- `detections.Detection`
- `kanban.Task`

Because relationships are string-based, model imports stay light and circular imports are avoided.

---

## Main URLs

```text
/robots/                         overview
/robots/map/                     operations map
/robots/api/map/                 JSON map payload
/robots/robots/                  robot list
/robots/robots/new/              create robot
/robots/robots/<id>/             robot detail
/robots/fleets/                  fleet list
/robots/fleets/new/              create fleet
/robots/fleets/<id>/             fleet detail
/robots/missions/                mission list
/robots/missions/new/            create mission
/robots/missions/<id>/           mission detail
/robots/maintenance/             maintenance list
/robots/models/                  robot models
/robots/docks/                   charging docks
```

---

## Future hardware integration

Implement automatic ingestion later by writing connector code that calls:

- `record_telemetry(...)`
- `record_robot_event(...)`
- `issue_command(...)`
- `acknowledge_command(...)`
- `complete_command(...)`
- `start_charging_session(...)`
- `end_charging_session(...)`

Suggested connector types:

- MQTT
- ROS2 bridge
- MAVLink
- WebSocket
- vendor HTTP API
- edge gateway
- Celery worker
- `workflows` node

Secrets should live in `gervazy`, ideally referenced through `api.ApiConnector` or a robot-specific connector subclass.

---

## Notes

- The app extends `oya/base.html` and uses the same Tailwind/Alpine style pattern as other toto apps.
- Map geometry helpers are defensive and support `geometry` or `point` on `Address`, plus `geometry` or `linestring` on `Route`.
- Raw telemetry can stay in Postgres for small/medium deployments. For high-frequency telemetry, keep summaries here and stream raw data into TimescaleDB, ClickHouse, object storage, or a message bus.
