from neomodel import (
    StructuredNode, StringProperty, IntegerProperty,
    BooleanProperty, DateProperty, RelationshipTo, RelationshipFrom
)
from toto.core.domain import DomainNode
from toto.core.graph.models import Federation
from toto.locations.graph.models import Address as AddressNode
from toto.locations.graph.models import Territory as TerritoryNode


class Community(DomainNode):
    __label__ = "Community"

    name = StringProperty(required=True)
    slug = StringProperty(index=True)
    org_type = StringProperty()
    established_year = IntegerProperty()
    is_autonomous = BooleanProperty(default=False)
    is_foreign = BooleanProperty(default=False)

    members = RelationshipTo("Person", "HAS_MEMBER")
    head = RelationshipTo("Person", "HEAD")

    # NEW
    federation = RelationshipTo(Federation, "BELONGS_TO")

    location = RelationshipTo(AddressNode, "LOCATED_AT")
    territory = RelationshipTo(TerritoryNode, "IN_TERRITORY")


class Person(DomainNode):
    __label__ = "Person"
    display_name = StringProperty(required=True)
    email = StringProperty()
    phone = StringProperty()
    date_of_birth = DateProperty()

    communities = RelationshipFrom("Community", "HAS_MEMBER")
    patron = RelationshipTo("Person", "MENTORED_BY")

    address = RelationshipTo(AddressNode, "RESIDES_AT")