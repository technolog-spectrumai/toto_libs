"""
Export logistics data as MapLayer overlays in the locations app.

Active packages are counted per destination address; active transports are
counted per current_location. Both layers are rebuilt from live DB state.
"""
from django.db.models import Count

from .models import Package, PackageStatus, Transport
from toto.locations.layer_export import refresh_layer_from_addresses
from toto.locations.models import Address

LAYER_ACTIVE_PACKAGES_SLUG = "layer-logistics-active-packages"
LAYER_TRANSPORT_DENSITY_SLUG = "layer-logistics-transport-density"

LAYER_EXPORT_SLUGS = [
    LAYER_ACTIVE_PACKAGES_SLUG,
    LAYER_TRANSPORT_DENSITY_SLUG,
]

_ACTIVE_PACKAGE_STATUSES = [
    PackageStatus.PICKED_UP,
    PackageStatus.IN_TRANSIT,
    PackageStatus.AT_HUB,
    PackageStatus.OUT_FOR_DELIVERY,
]


def export_active_packages_layer():
    qs = (
        Package.objects
        .filter(status__in=_ACTIVE_PACKAGE_STATUSES, destination__isnull=False)
        .values("destination_id")
        .annotate(count=Count("id"))
    )
    addr_ids = [r["destination_id"] for r in qs]
    addr_map = {
        a.pk: a
        for a in Address.objects.filter(pk__in=addr_ids, geometry__isnull=False)
    }
    entries = [
        (addr_map[r["destination_id"]], r["count"], {"address_id": r["destination_id"]})
        for r in qs
        if r["destination_id"] in addr_map
    ]
    return refresh_layer_from_addresses(
        slug=LAYER_ACTIVE_PACKAGES_SLUG,
        name="Logistics — Active Packages",
        unit="packages",
        description="Number of packages currently in transit per destination address.",
        entries=entries,
    )


def export_transport_density_layer():
    qs = (
        Transport.objects
        .filter(is_active=True, current_location__isnull=False)
        .values("current_location_id")
        .annotate(count=Count("id"))
    )
    addr_ids = [r["current_location_id"] for r in qs]
    addr_map = {
        a.pk: a
        for a in Address.objects.filter(pk__in=addr_ids, geometry__isnull=False)
    }
    entries = [
        (addr_map[r["current_location_id"]], r["count"], {"address_id": r["current_location_id"]})
        for r in qs
        if r["current_location_id"] in addr_map
    ]
    return refresh_layer_from_addresses(
        slug=LAYER_TRANSPORT_DENSITY_SLUG,
        name="Logistics — Transport Density",
        unit="transports",
        description="Number of active transports currently at each location.",
        entries=entries,
    )
