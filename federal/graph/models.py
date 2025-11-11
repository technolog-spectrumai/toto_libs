# graph.py
from neomodel import (
    StructuredNode,
    StructuredRel,
    StringProperty,
    BooleanProperty,
    DateTimeProperty,
    RelationshipTo,
    RelationshipFrom,
    JSONProperty
)

# -----------------------------
# Relationship Models (Edges)
# -----------------------------

class RecognizesRel(StructuredRel):
    """
    Federation -> FederatedIdentity
    """
    created_at = DateTimeProperty(default_now=True)
    metadata = JSONProperty()


class BelongsToRel(StructuredRel):
    """
    FederatedIdentity -> Federation
    """
    created_at = DateTimeProperty(default_now=True)
    metadata = JSONProperty()


# -----------------------------
# Node Models
# -----------------------------

class Federation(StructuredNode):
    uid = StringProperty(unique_index=True, required=True)   # SQL Federation PK
    name = StringProperty(required=True, index=True)
    description = StringProperty()
    created_at = DateTimeProperty(default_now=True)
    active = BooleanProperty(default=True)
    metadata = JSONProperty()

    # Relationships
    identities = RelationshipTo('FederatedIdentity', 'RECOGNIZES', model=RecognizesRel)


class FederatedIdentity(StructuredNode):
    uid = StringProperty(unique_index=True, required=True)   # SQL FederatedIdentity PK (UUID)
    name = StringProperty()
    created_at = DateTimeProperty(default_now=True)
    issuer = StringProperty()  # federation URL
    metadata = JSONProperty()
    user_id = StringProperty(index=True, required=False)     # optional SQL User PK

    # Relationships
    federation = RelationshipTo('Federation', 'BELONGS_TO', model=BelongsToRel)

