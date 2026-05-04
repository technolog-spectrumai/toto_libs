from neomodel import StringProperty, RelationshipTo, RelationshipFrom
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


# ---------------------------------------------------------
# ROUTE
# ---------------------------------------------------------

class Route(DomainNode):
    __label__ = "Route"

    name = StringProperty()

    # WKT: "MULTILINESTRING((...))"
    geometry = StringProperty(required=True)

    start_address = RelationshipTo("Address", "STARTS_AT")
    end_address = RelationshipTo("Address", "ENDS_AT")
