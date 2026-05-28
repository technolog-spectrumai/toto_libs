"""
Export inventory data as MapLayer overlays in the locations app.

Active inventory sites are counted per address; tracked real-world objects
are counted per location address. Both layers are rebuilt from live DB state.
"""
from django.db.models import Count

from .models import InventorySite, RealWorldObject
from toto.locations.layer_export import refresh_layer_from_addresses
from toto.locations.models import Address

LAYER_INVENTORY_SITES_SLUG = "layer-inventory-sites"
LAYER_INVENTORY_OBJECTS_SLUG = "layer-inventory-objects"

LAYER_EXPORT_SLUGS = [
    LAYER_INVENTORY_SITES_SLUG,
    LAYER_INVENTORY_OBJECTS_SLUG,
]


def export_inventory_sites_layer():
    qs = (
        InventorySite.objects
        .filter(is_active=True, address__isnull=False)
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
        slug=LAYER_INVENTORY_SITES_SLUG,
        name="Inventory — Sites",
        unit="sites",
        description="Number of active inventory sites per location.",
        entries=entries,
    )


def export_inventory_objects_layer():
    qs = (
        RealWorldObject.objects
        .filter(location__isnull=False)
        .values("location_id")
        .annotate(count=Count("id"))
    )
    addr_ids = [r["location_id"] for r in qs]
    addr_map = {
        a.pk: a
        for a in Address.objects.filter(pk__in=addr_ids, geometry__isnull=False)
    }
    entries = [
        (addr_map[r["location_id"]], r["count"], {"address_id": r["location_id"]})
        for r in qs
        if r["location_id"] in addr_map
    ]
    return refresh_layer_from_addresses(
        slug=LAYER_INVENTORY_OBJECTS_SLUG,
        name="Inventory — Objects",
        unit="objects",
        description="Number of tracked inventory objects per location.",
        entries=entries,
    )
