from neomodel import (
    StructuredNode,
    UniqueIdProperty,
    StringProperty,
    JSONProperty,
    DateTimeProperty,
    RelationshipTo,
    RelationshipFrom,
)


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
    related = RelationshipTo('Note', 'RELATED_TO')   # note ↔ note
    tags = RelationshipTo('Tag', 'TAGGED_WITH')      # note ↔ tag
