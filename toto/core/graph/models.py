from neomodel import (
    StructuredNode, StringProperty, BooleanProperty,
    DateTimeProperty, RelationshipTo
)
from toto.core.graph.base import DomainNode


class Federation(DomainNode):
    __label__ = "Federation"

    name = StringProperty(required=True, unique_index=True)
    description = StringProperty()
    logo = StringProperty()
    created_at = DateTimeProperty()
    active = BooleanProperty(default=True)

