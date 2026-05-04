from neomodel import (
    StructuredNode, StringProperty, IntegerProperty,
    BooleanProperty, DateProperty, RelationshipTo, RelationshipFrom
)
from toto.core.domain import DomainNode
from toto.core.graph.models import Federation


class Community(DomainNode):
    __label__ = "Community"

    name = StringProperty(required=True)
    slug = StringProperty(index=True)
    org_type = StringProperty()
    established_year = IntegerProperty()
    is_autonomous = BooleanProperty(default=False)
    is_foreign = BooleanProperty(default=False)

    members = RelationshipTo("CommunityMember", "HAS_MEMBER")
    head = RelationshipTo("CommunityMember", "HEAD")

    # NEW
    federation = RelationshipTo(Federation, "BELONGS_TO")



class CommunityMember(DomainNode):
    __label__ = "CommunityMember"
    display_name = StringProperty(required=True)
    email = StringProperty()
    phone = StringProperty()
    date_of_birth = DateProperty()

    communities = RelationshipFrom("Community", "HAS_MEMBER")
    patron = RelationshipTo("CommunityMember", "MENTORED_BY")
