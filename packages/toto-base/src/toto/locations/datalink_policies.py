"""Geography. The noosphere allowlists, revived and extended with their references.

The five dead ``sync_adapters.py`` files declared `name` and `geometry` for Territory,
Zone and Route, and slug/unit/min/max for MapLayer. Those lists were right about what
a human wants to see move; they had to omit every FK because noosphere had no way to
express one. Datalink does, so the references are declared here explicitly.

`geometry`/`center` are in ``optional_fields`` because a BUILD_GEO=0 host migrates
toto.locations from ``migrations_nogis`` and those columns are absent from the model
entirely — not null, *absent*. Address keeps plain latitude/longitude on every build,
which is why a GIS-off receiver still gets usable coordinates.

The asymmetry to know about: Territory, Zone, Route and MapLayerPolygon have
NOT NULL geometry on a GIS build. So a GIS-on receiver cannot accept these rows from a
GIS-off peer, and preflight refuses that pairing rather than failing mid-stage.
"""
from toto.datalink.registry import (
    IDENTITY_UID,
    STAGE_MAPS,
    STAGE_PLACES,
    SyncPolicy,
    register,
)

_GEO = ("geometry",)

# Declaration order inside a stage is the order rows are written. Address first: it is
# the only model here nothing else in the stage depends on, and three others point at it.
register(SyncPolicy(
    "locations.Address", stage=STAGE_PLACES, identity=IDENTITY_UID,
    fields=(
        "country_name", "state_or_province_name", "locality_name", "street",
        "building", "apartment", "latitude", "longitude", "geometry", "metadata", "note",
    ),
    optional_fields=_GEO,
    bulk_safe=False,
    notes=(
        "bulk_safe=False on purpose: Address.save() re-derives latitude/longitude from "
        "geometry. That is a convergent transform the peer already applied, so letting "
        "it run is harmless and skipping it would leave lat/lon inconsistent with the "
        "shape on a GIS receiver. It is also why the merge base stores a local checksum "
        "as well as a peer one — save() changing the row must not read as a local edit."
    ),
))

register(SyncPolicy(
    "locations.Territory", stage=STAGE_PLACES, identity=IDENTITY_UID,
    fields=("name", "geometry", "capital", "metadata"),
    optional_fields=_GEO, bulk_safe=True,
    notes="geometry is NOT NULL on a GIS build — a GIS-off peer cannot supply it.",
))
register(SyncPolicy(
    "locations.Zone", stage=STAGE_PLACES, identity=IDENTITY_UID,
    fields=("name", "geometry", "territory", "metadata"),
    optional_fields=_GEO, bulk_safe=True,
))
register(SyncPolicy(
    "locations.RouteChain", stage=STAGE_PLACES, identity=IDENTITY_UID,
    fields=("name", "description", "metadata"), bulk_safe=True,
))
register(SyncPolicy(
    "locations.Route", stage=STAGE_PLACES, identity=IDENTITY_UID,
    fields=(
        "name", "geometry", "route_chain", "sequence",
        "start_address", "end_address", "metadata", "notes",
    ),
    optional_fields=_GEO, bulk_safe=True,
))

# MapLayer.owner points at people.Person, so these two land after the people stage.
# That single reference is the whole reason `maps` is a stage of its own.
register(SyncPolicy(
    "locations.MapLayer", stage=STAGE_MAPS, identity=IDENTITY_UID,
    unique_guards=(("slug",),),
    fields=(
        "name", "slug", "description", "unit", "min_value", "max_value",
        "style", "inverted_importance", "half_range", "is_active", "owner",
    ),
    bulk_safe=True,
    notes="slug is written explicitly so MapLayer's own slug derivation never runs.",
))
register(SyncPolicy(
    "locations.MapLayerPolygon", stage=STAGE_MAPS, identity=IDENTITY_UID,
    fields=("layer", "name", "geometry", "center", "value", "properties"),
    optional_fields=("geometry", "center"), bulk_safe=True,
    notes=(
        "The high-cardinality model in the scope — thousands of rows per imported "
        "layer. `layer` is NOT NULL, so a polygon whose layer failed to resolve is "
        "withheld rather than orphaned."
    ),
))
