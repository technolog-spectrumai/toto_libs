from neomodel import (
    StructuredNode,
    StructuredRel,
    UniqueIdProperty,
    StringProperty,
    JSONProperty,
    DateTimeProperty,
    RelationshipTo,
    RelationshipFrom
)

# Relationship model for edges with metadata
class NoteRel(StructuredRel):
    uid = UniqueIdProperty()
    relation_type = StringProperty(required=True)   # e.g., "REFERENCES", "TAGGED_WITH"
    metadata = JSONProperty()                       # extra info (confidence, source, etc.)
    created_at = DateTimeProperty(default_now=True)


class Note(StructuredNode):
    """
    Represents an Intel Note in the graph.
    """
    uid = UniqueIdProperty()
    title = StringProperty(required=True, index=True)
    content = StringProperty()                      # main text of the note
    metadata = JSONProperty()                       # audit info, provenance, etc.
    created_at = DateTimeProperty(default_now=True)
    category = StringProperty()                     # e.g., "Intel", "Observation", "Report"

    # Relationships
    references = RelationshipTo('Note', 'REFERENCES', model=NoteRel)
    related = RelationshipTo('Note', 'RELATED_TO', model=NoteRel)
    tagged = RelationshipTo('Tag', 'TAGGED_WITH', model=NoteRel)


class Tag(StructuredNode):
    """
    Represents a tag or keyword attached to notes.
    """
    uid = UniqueIdProperty()
    name = StringProperty(required=True, unique_index=True)
    metadata = JSONProperty()
    created_at = DateTimeProperty(default_now=True)

    # Reverse relationship
    notes = RelationshipFrom('Note', 'TAGGED_WITH', model=NoteRel)
