from neomodel import (
    StringProperty,
    DateTimeProperty,
    BooleanProperty,
    RelationshipTo,
    RelationshipFrom,
)
from toto.core.domain import DomainNode
from toto.socialhub.graph.models import Person as MemberNode


class EventCategoryNode(DomainNode):
    __label__ = "EventCategory"

    name = StringProperty(required=True)
    # description intentionally NOT stored

    events = RelationshipFrom("EventNode", "HAS_CATEGORY")


class EventNode(DomainNode):
    __label__ = "Event"

    title = StringProperty(required=True)
    location = StringProperty()

    start_time = DateTimeProperty()
    end_time = DateTimeProperty()

    public = BooleanProperty(default=True)

    organizer = RelationshipTo(MemberNode, "ORGANIZED_BY")
    category = RelationshipTo(EventCategoryNode, "HAS_CATEGORY")
