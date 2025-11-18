from neomodel import (
    StructuredNode,
    UniqueIdProperty,
    StringProperty,
    JSONProperty,
    DateTimeProperty,
    RelationshipTo,
    RelationshipFrom,
)
from events.graph.models import Event
from community.graph.models import Community, CommunityMember
from finance.graph.models import Account, Transaction
from portfolio.graph.models import Company


class Tag(StructuredNode):
    """
    Represents a tag or keyword attached to notes.
    """
    uid = UniqueIdProperty()
    name = StringProperty(required=True, unique_index=True)
    created_at = DateTimeProperty(default_now=True)

    notes = RelationshipFrom('Note', 'TAGGED_WITH')


class Note(StructuredNode):
    """
    Represents an Intel Note in the graph database.
    """
    uid = UniqueIdProperty()
    title = StringProperty(required=True, index=True)
    content = StringProperty()
    metadata = JSONProperty()
    created_at = DateTimeProperty(default_now=True)
    category = StringProperty()

    # Relationships
    tags = RelationshipTo('Tag', 'TAGGED_WITH')
    event = RelationshipTo(Event, 'DESCRIBES_EVENT')
    subject_community = RelationshipTo(Community, 'HAS_SUBJECT')
    subject_member = RelationshipTo(CommunityMember, 'HAS_SUBJECT')
    subject_company = RelationshipTo(Company, 'HAS_SUBJECT')
