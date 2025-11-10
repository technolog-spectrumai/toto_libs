from neomodel import (
    StructuredNode,
    UniqueIdProperty,
    StringProperty,
    JSONProperty,
    DateTimeProperty,
    RelationshipTo,
    RelationshipFrom
)

class Node(StructuredNode):
    """
    Graph node representing an entity in the system.
    """
    uid = UniqueIdProperty()
    name = StringProperty(required=True, index=True)

    # Arbitrary JSON data (main payload)
    data = JSONProperty()

    # Extra metadata JSON (audit, descriptive info, etc.)
    metadata = JSONProperty()

    created_at = DateTimeProperty(default_now=True)
    category = StringProperty()

    # Outgoing edges
    edges = RelationshipTo('Edge', 'HAS_EDGE')


class Edge(StructuredNode):
    """
    Graph edge representing a relationship between nodes.
    """
    uid = UniqueIdProperty()
    name = StringProperty(required=True, index=True)

    # Only metadata, no data payload
    metadata = JSONProperty()

    created_at = DateTimeProperty(default_now=True)

    # Relationships
    source = RelationshipFrom('Node', 'HAS_EDGE')
    target = RelationshipTo('Node', 'POINTS_TO')
