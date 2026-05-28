"""
Export detection data as MapLayer overlays in the locations app.

Active detection counts and average severity scores are computed per address
and written as MapLayer polygons. Only detections with a direct address link
and active status (new/acknowledged/handling) are included.
"""
from django.db.models import Count, Avg, Case, When, Value, FloatField

from .models import Detection
from toto.locations.layer_export import refresh_layer_from_addresses
from toto.locations.models import Address

LAYER_DETECTIONS_ACTIVE_SLUG = "layer-detections-active"
LAYER_DETECTIONS_SEVERITY_SLUG = "layer-detections-severity"

LAYER_EXPORT_SLUGS = [
    LAYER_DETECTIONS_ACTIVE_SLUG,
    LAYER_DETECTIONS_SEVERITY_SLUG,
]

_ACTIVE_STATUSES = ["new", "acknowledged", "handling"]

_SEVERITY_SCORE = Case(
    When(severity="low", then=Value(1.0)),
    When(severity="medium", then=Value(2.0)),
    When(severity="high", then=Value(3.0)),
    When(severity="critical", then=Value(4.0)),
    default=Value(1.0),
    output_field=FloatField(),
)


def export_active_detections_layer():
    qs = (
        Detection.objects
        .filter(status__in=_ACTIVE_STATUSES, address__isnull=False)
        .values("address_id")
        .annotate(count=Count("id"))
    )
    addr_ids = [r["address_id"] for r in qs]
    addr_map = {
        a.pk: a
        for a in Address.objects.filter(pk__in=addr_ids, geometry__isnull=False)
    }
    entries = [
        (addr_map[r["address_id"]], r["count"], {"address_id": r["address_id"]})
        for r in qs
        if r["address_id"] in addr_map
    ]
    return refresh_layer_from_addresses(
        slug=LAYER_DETECTIONS_ACTIVE_SLUG,
        name="Detections — Active",
        unit="detections",
        description="Number of active detections (new / acknowledged / handling) per address.",
        entries=entries,
    )


def export_detection_severity_layer():
    qs = (
        Detection.objects
        .filter(status__in=_ACTIVE_STATUSES, address__isnull=False)
        .values("address_id")
        .annotate(avg_severity=Avg(_SEVERITY_SCORE))
    )
    addr_ids = [r["address_id"] for r in qs]
    addr_map = {
        a.pk: a
        for a in Address.objects.filter(pk__in=addr_ids, geometry__isnull=False)
    }
    entries = [
        (addr_map[r["address_id"]], r["avg_severity"], {"address_id": r["address_id"]})
        for r in qs
        if r["address_id"] in addr_map
    ]
    return refresh_layer_from_addresses(
        slug=LAYER_DETECTIONS_SEVERITY_SLUG,
        name="Detections — Severity",
        unit="score",
        description="Average severity score per address (1=low, 2=medium, 3=high, 4=critical) for active detections.",
        min_value=1.0,
        max_value=4.0,
        entries=entries,
    )
