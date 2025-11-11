from neomodel import (
    StructuredNode,
    StringProperty,
    DateTimeProperty,
    BooleanProperty,
    RelationshipTo,
)

from portfolio.graph.models import Company
from community.graph.models import CommunityMember


class Event(StructuredNode):
    uid = StringProperty(unique_index=True, required=True)   # mirror SQL PK or UUID
    title = StringProperty(required=True, index=True)
    description = StringProperty()
    location = StringProperty()
    start_time = DateTimeProperty(required=True)
    end_time = DateTimeProperty(required=True)
    public = BooleanProperty(default=True)

    # Instead of a relationship to EventCategory, just store the category name
    event_type = StringProperty(index=True)   # copy of category.name

    # Relationships
    company = RelationshipTo(Company, 'RELATED_TO')
    organizer = RelationshipTo(CommunityMember, 'ORGANIZED_BY')
