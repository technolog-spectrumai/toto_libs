from toto.noosphere.adapters import BaseSyncAdapter
from toto.noosphere.registry import register_sync_adapter

from .models import Territory, Zone, MapLayer, Route


@register_sync_adapter
class TerritorySyncAdapter(BaseSyncAdapter):
    model = Territory
    allowed_fields = [
        "name",
        "geometry",
    ]


@register_sync_adapter
class ZoneSyncAdapter(BaseSyncAdapter):
    model = Zone
    allowed_fields = [
        "name",
        "geometry",
    ]


@register_sync_adapter
class MapLayerSyncAdapter(BaseSyncAdapter):
    model = MapLayer
    allowed_fields = [
        "name",
        "slug",
        "description",
        "unit",
        "min_value",
        "max_value",
    ]


@register_sync_adapter
class RouteSyncAdapter(BaseSyncAdapter):
    model = Route
    allowed_fields = [
        "name",
        "geometry",
        "sequence",
    ]
