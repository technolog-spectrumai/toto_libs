from neomodel import BooleanProperty, FloatProperty, IntegerProperty, StringProperty, RelationshipTo, RelationshipFrom
from toto.core.domain import DomainNode


# ---------------------------------------------------------
# ADDRESS
# ---------------------------------------------------------

class Address(DomainNode):
    __label__ = "Address"

    country_name = StringProperty(required=True)
    state_or_province_name = StringProperty(required=True)
    locality_name = StringProperty(required=True)
    street = StringProperty(required=True)
    building = StringProperty(required=True)
    apartment = StringProperty()

    # Geometry stored as WKT string: "POINT(lon lat)"
    geometry = StringProperty()

    # Reverse relations
    territory_capitals = RelationshipFrom("Territory", "HAS_CAPITAL")
    route_starts = RelationshipFrom("Route", "STARTS_AT")
    route_ends = RelationshipFrom("Route", "ENDS_AT")


# ---------------------------------------------------------
# TERRITORY
# ---------------------------------------------------------

class Territory(DomainNode):
    __label__ = "Territory"

    name = StringProperty(required=True)

    # WKT: "POLYGON((...))"
    geometry = StringProperty(required=True)

    capital = RelationshipTo("Address", "HAS_CAPITAL")
    zones = RelationshipFrom("Zone", "IN_TERRITORY")


# ---------------------------------------------------------
# ROUTE
# ---------------------------------------------------------

class Route(DomainNode):
    __label__ = "Route"

    name = StringProperty()
    sequence = IntegerProperty(default=0)

    # WKT: "MULTILINESTRING((...))"
    geometry = StringProperty(required=True)

    route_chain = RelationshipTo("RouteChain", "PART_OF_CHAIN")
    start_address = RelationshipTo("Address", "STARTS_AT")
    end_address = RelationshipTo("Address", "ENDS_AT")


# ---------------------------------------------------------
# ZONE
# ---------------------------------------------------------

class Zone(DomainNode):
    __label__ = "Zone"

    name = StringProperty(required=True)

    # WKT: "MULTIPOLYGON(((...)))"
    geometry = StringProperty(required=True)

    territory = RelationshipTo("Territory", "IN_TERRITORY")


# ---------------------------------------------------------
# ROUTE CHAIN
# ---------------------------------------------------------

class RouteChain(DomainNode):
    __label__ = "RouteChain"

    name = StringProperty(required=True)
    description = StringProperty()

    routes = RelationshipFrom("Route", "PART_OF_CHAIN")


# ---------------------------------------------------------
# MAP LAYER
# ---------------------------------------------------------

class MapLayer(DomainNode):
    __label__ = "MapLayer"

    name = StringProperty(required=True)
    slug = StringProperty(required=True, unique_index=True)
    description = StringProperty()
    unit = StringProperty()
    min_value = FloatProperty()
    max_value = FloatProperty()
    style = StringProperty()
    inverted_importance = BooleanProperty(default=False)
    half_range = BooleanProperty(default=False)
    is_active = BooleanProperty(default=True)

    polygons = RelationshipFrom("MapLayerPolygon", "IN_LAYER")


# ---------------------------------------------------------
# MAP LAYER POLYGON
# ---------------------------------------------------------

class MapLayerPolygon(DomainNode):
    __label__ = "MapLayerPolygon"

    name = StringProperty()
    value = FloatProperty(required=True)
    properties = StringProperty()

    # WKT strings
    geometry = StringProperty(required=True)
    center = StringProperty()

    layer = RelationshipTo("MapLayer", "IN_LAYER")
