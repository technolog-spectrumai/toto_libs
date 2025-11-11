from neomodel import (
    StructuredNode,
    StructuredRel,
    StringProperty,
    JSONProperty,
    DateTimeProperty,
    RelationshipTo,
    RelationshipFrom
)

# Import your social graph domain
from community.graph.models import Community, CommunityMember

# -----------------------------
# Relationship Models (Edges)
# -----------------------------

class TransactionRel(StructuredRel):
    metadata = JSONProperty()
    created_at = DateTimeProperty(default_now=True)


class OwnershipRel(StructuredRel):
    metadata = JSONProperty()
    created_at = DateTimeProperty(default_now=True)


class ManagementRel(StructuredRel):
    metadata = JSONProperty()
    created_at = DateTimeProperty(default_now=True)


# -----------------------------
# Node Models
# -----------------------------

class Account(StructuredNode):
    uid = StringProperty(unique_index=True, required=True)   # reuse SQL UUID
    name = StringProperty(required=True, unique_index=True)
    balance = StringProperty()
    currency_symbol = StringProperty(required=True)          # e.g. "USD"
    currency_name = StringProperty()                         # e.g. "US Dollar"
    active = StringProperty(default="true")
    created_at = DateTimeProperty(default_now=True)
    metadata = JSONProperty()

    # Relationships
    outgoing = RelationshipFrom('Transaction', 'SOURCE', model=TransactionRel)
    incoming = RelationshipFrom('Transaction', 'DESTINATION', model=TransactionRel)

    # Explicit links
    owner = RelationshipTo(CommunityMember, 'OWNED_BY', model=OwnershipRel)
    manager = RelationshipTo(CommunityMember, 'MANAGED_BY', model=ManagementRel)


class Transaction(StructuredNode):
    uid = StringProperty(unique_index=True, required=True)  # reuse SQL UUID
    name = StringProperty(required=True)
    amount = StringProperty()
    currency_symbol = StringProperty(required=True)
    currency_name = StringProperty()
    timestamp = DateTimeProperty(default_now=True)
    metadata = JSONProperty()

    # Relationships
    source_account = RelationshipTo('Account', 'SOURCE', model=TransactionRel)
    destination_account = RelationshipTo('Account', 'DESTINATION', model=TransactionRel)
