import json
from datetime import timedelta

from django.utils import timezone

from toto.kanban.models import Campaign, Column, Mission, Practitioner, Project, ProjectCommitment, Task
from toto.people.models import Person


def first_or_create(model, defaults=None, **lookup):
    obj = model.objects.filter(**lookup).order_by("pk").first()
    if obj:
        return obj, False
    params = {**lookup, **(defaults or {})}
    return model.objects.create(**params), True


def person_for_user(user):
    if not getattr(user, "is_authenticated", False):
        return None
    try:
        return user.community_profile
    except Exception:
        return None


def geometry_json(geometry):
    if not geometry:
        return None
    return json.loads(geometry.geojson)


def detection_map_feature(detection):
    geometry = detection.map_geometry
    if not geometry:
        return None
    return {
        "id": str(detection.pk),
        "title": detection.title,
        "type": detection.get_detection_type_display(),
        "severity": detection.severity,
        "status": detection.get_status_display(),
        "location": detection.location_label or "",
        "url": detection.get_absolute_url() if hasattr(detection, "get_absolute_url") else "",
        "geometry": geometry_json(geometry),
    }


def _get_or_create_practitioner(project, person, role):
    """Get or create a Practitioner for person, ensuring a ProjectCommitment to project."""
    prac, _ = Practitioner.objects.get_or_create(
        person=person,
        defaults={"role": role, "is_active": True},
    )
    ProjectCommitment.objects.get_or_create(
        practitioner=prac,
        project=project,
        defaults={"hours_per_day": 4, "is_active": True},
    )
    return prac


def ensure_detection_mitigation_task(detection, *, owner=None, reviewer=None):
    if detection.mitigation_task_id:
        return detection.mitigation_task

    owner = owner or detection.reported_by or Person.objects.order_by("pk").first()
    if not owner:
        return None

    project, _ = first_or_create(
        Project,
        name="Detection Mitigation",
        defaults={
            "description": "Kanban project for detection mitigation and help requests.",
            "project_lead": owner,
        },
    )

    # Ensure owner has a practitioner seat
    owner_prac = _get_or_create_practitioner(project, owner, Practitioner.ROLE_MANAGER)

    columns = {}
    for name, position, can_add in [
        ("To Do", 1, True),
        ("In Progress", 2, False),
        ("Review", 3, False),
        ("Done", 4, False),
    ]:
        column, _ = first_or_create(
            Column,
            project=project,
            name=name,
            defaults={"position": position, "can_add_task": can_add},
        )
        column.auditors.add(owner_prac)
        columns[name] = column

    campaign, _ = first_or_create(
        Campaign,
        project=project,
        name="Field Response",
        defaults={
            "description": "Mitigation campaign generated from detections.",
            "start_date": timezone.now().date(),
            "end_date": (timezone.now() + timedelta(days=30)).date(),
            "owner": owner,
            "metadata": {"source": "detections"},
        },
    )
    mission, _ = first_or_create(
        Mission,
        campaign=campaign,
        title="Mitigate Active Detections",
        defaults={
            "description": "Resolve active detection incidents through normal Kanban task flow.",
            "urgency": 3,
            "impact": 3,
            "owner": owner,
            "metadata": {"source": "detections"},
        },
    )

    reviewer_prac = None
    if reviewer is not None:
        reviewer_prac = _get_or_create_practitioner(project, reviewer, Practitioner.ROLE_REVIEWER)

    task = Task.objects.create(
        mission=mission,
        column=columns["To Do"],
        title=f"Mitigate: {detection.title}",
        description=detection.description,
        reviewer=reviewer_prac,
        due_date=(timezone.now() + timedelta(days=3)).date(),
        weight=3 if detection.severity in ("high", "critical") else 2,
        metadata={
            "source": "detection",
            "detection_id": str(detection.pk),
            "severity": detection.severity,
        },
    )
    detection.mitigation_task = task
    detection.save(update_fields=["mitigation_task", "updated_at"])
    return task


def skill_metadata(required_skills):
    return [
        {
            "id": skill.pk,
            "title": skill.title,
            "slug": skill.slug,
            "group": skill.group.title,
        }
        for skill in (required_skills or [])
    ]


def create_detection_help_task(
    detection,
    *,
    mission,
    owner=None,
    reviewer=None,
    title="",
    description="",
    required_skills=None,
):
    project = mission.campaign.project
    column = (
        Column.objects
        .filter(project=project)
        .order_by("position", "pk")
        .first()
    )
    if not column:
        column = Column.objects.create(project=project, name="To Do", position=1, can_add_task=True)

    metadata = {
        "source": "detection_help",
        "detection_id": str(detection.pk),
        "severity": detection.severity,
    }
    if owner:
        metadata["requested_by"] = {"id": owner.pk, "name": str(owner)}
    skills = skill_metadata(required_skills)
    if skills:
        metadata["required_skills"] = skills

    reviewer_prac = None
    if reviewer is not None:
        reviewer_prac = _get_or_create_practitioner(project, reviewer, Practitioner.ROLE_REVIEWER)

    task = Task.objects.create(
        mission=mission,
        column=column,
        title=title or f"Help with: {detection.title}",
        description=description or detection.description,
        reviewer=reviewer_prac,
        due_date=(timezone.now() + timedelta(days=3)).date(),
        weight=3 if detection.severity in ("high", "critical") else 2,
        metadata=metadata,
    )
    detection.mitigation_task = task
    detection.save(update_fields=["mitigation_task", "updated_at"])
    return task
