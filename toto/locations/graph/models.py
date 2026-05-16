from neomodel import (
    BooleanProperty,
    DateTimeProperty,
    FloatProperty,
    IntegerProperty,
    StringProperty,
    RelationshipTo,
    RelationshipFrom,
)

from toto.core.graph.base import DomainNode

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
    visits = RelationshipFrom("Visit", "VISITED_LOCATION")


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

    travels = RelationshipFrom("Travel", "USES_ROUTE")


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
# TRAVEL
# ---------------------------------------------------------

class Travel(DomainNode):
    __label__ = "Travel"

    info = StringProperty()
    starts_at = DateTimeProperty(required=True)
    ends_at = DateTimeProperty(required=True)

    route = RelationshipTo("Route", "USES_ROUTE")
    participants = RelationshipTo("toto.socialhub.graph.models.Person", "HAS_PARTICIPANT")


# ---------------------------------------------------------
# VISIT
# ---------------------------------------------------------

class Visit(DomainNode):
    __label__ = "Visit"

    review = StringProperty()
    score = IntegerProperty()

    participant = RelationshipTo("toto.socialhub.graph.models.Person", "HAS_PARTICIPANT")
    location = RelationshipTo("Address", "VISITED_LOCATION")


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

    owner = RelationshipTo(
        "toto.socialhub.graph.models.Person",
        "OWNS_LAYER"
    )
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