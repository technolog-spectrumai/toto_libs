from neomodel import (
    StructuredNode,
    StringProperty,
    DateTimeProperty,
    BooleanProperty,
    RelationshipTo,
    RelationshipFrom,
)
from toto.socialhub.graph.models import CommunityMember as MemberNode


class EventCategoryNode(StructuredNode):
    uuid = StringProperty(unique=True)
    name = StringProperty()
    # description intentionally NOT stored

    events = RelationshipFrom("EventNode", "HAS_CATEGORY")


class EventNode(StructuredNode):
    uuid = StringProperty(unique=True)

    title = StringProperty()
    location = StringProperty()

    start_time = DateTimeProperty()
    end_time = DateTimeProperty()

    public = BooleanProperty(default=True)

    organizer = RelationshipTo(MemberNode, "ORGANIZED_BY")
    category = RelationshipTo(EventCategoryNode, "HAS_CATEGORY")
