"""
Utility for creating MapLayer overlays from address-based point data.

Each source app (weather, logistics, inventory, detections) calls
refresh_layer_from_addresses() with a list of (Address, float, dict) entries.
The address point is buffered into a small circular polygon so it renders
visibly on the map alongside the existing polygon-based layers.
"""
from .models import MapLayer, MapLayerPolygon

POINT_BUFFER_DEGREES = 0.04  # ~4.4 km at mid-latitudes; adjust per deployment scale


def refresh_layer_from_addresses(
    *,
    slug: str,
    name: str,
    unit: str,
    entries,
    description: str = "",
    min_value=None,
    max_value=None,
    inverted_importance: bool = False,
    half_range: bool = False,
    style: dict | None = None,
):
    """
    Create or refresh a MapLayer whose polygons are buffered address points.

    `entries` is an iterable of (Address, float_value, props_dict) tuples.
    Addresses without geometry are skipped. All existing polygons for the
    layer are replaced on each call.

    Returns (layer, polygon_count).
    """
    layer, _ = MapLayer.objects.update_or_create(
        slug=slug,
        defaults={
            "name": name,
            "description": description,
            "unit": unit,
            "min_value": min_value,
            "max_value": max_value,
            "style": style or {},
            "inverted_importance": inverted_importance,
            "half_range": half_range,
            "is_active": True,
        },
    )

    layer.polygons.all().delete()

    polygons = []
    for address, value, props in entries:
        if not address.geometry:
            continue
        polygons.append(MapLayerPolygon(
            layer=layer,
            name=str(address),
            geometry=address.geometry.buffer(POINT_BUFFER_DEGREES),
            center=address.geometry,
            value=float(value),
            properties=props or {},
        ))

    MapLayerPolygon.objects.bulk_create(polygons)
    return layer, len(polygons)
