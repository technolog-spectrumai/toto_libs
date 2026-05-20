import json
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from toto.bazaar.models import Product, Shop
from toto.kanban.models import Campaign, Column, Mission, Project, Task
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
            "owner": owner,
        },
    )
    if owner.user_id:
        project.collaborators.add(owner.user)

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
            defaults={
                "position": position,
                "can_add_task": can_add,
            },
        )
        if owner.user_id:
            column.auditors.add(owner.user)
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

    task = Task.objects.create(
        mission=mission,
        column=columns["To Do"],
        title=f"Mitigate: {detection.title}",
        description=detection.description,
        reviewer=reviewer,
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


def create_detection_help_task(detection, *, mission, owner=None, reviewer=None, title="", description=""):
    project = mission.campaign.project
    column = (
        Column.objects
        .filter(project=project)
        .order_by("position", "pk")
        .first()
    )
    if not column:
        column = Column.objects.create(project=project, name="To Do", position=1, can_add_task=True)
    task = Task.objects.create(
        mission=mission,
        column=column,
        title=title or f"Help with: {detection.title}",
        description=description or detection.description,
        assignee=owner,
        reviewer=reviewer,
        due_date=(timezone.now() + timedelta(days=3)).date(),
        weight=3 if detection.severity in ("high", "critical") else 2,
        metadata={
            "source": "detection_help",
            "detection_id": str(detection.pk),
            "severity": detection.severity,
        },
    )
    detection.mitigation_task = task
    detection.save(update_fields=["mitigation_task", "updated_at"])
    return task


def create_detection_service_request(
    detection,
    *,
    requester=None,
    title="",
    description="",
    price=Decimal("0.00"),
    currency="PLN",
):
    currency = (currency or "PLN").upper()[:3]
    shop = Shop.objects.filter(is_active=True).order_by("pk").first()
    if not shop:
        shop = Shop.objects.create(
            name="Detection Services",
            description="Service requests outsourced from field detections.",
            owner=requester,
            currency=currency,
            is_active=True,
        )

    product = Product.objects.create(
        shop=shop,
        name=title or f"Help with: {detection.title}",
        summary=f"Service request for detection {detection.title}"[:500],
        description=description or detection.description,
        product_type="service",
        status="published",
        price=price or Decimal("0.00"),
        currency=currency or shop.currency,
        is_public=True,
        is_regulated=False,
        origin_address=detection.address,
        metadata={
            "source": "detection_help",
            "detection_id": str(detection.pk),
            "severity": detection.severity,
        },
    )
    detection.outsourced_service = product
    detection.save(update_fields=["outsourced_service", "updated_at"])
    return product
