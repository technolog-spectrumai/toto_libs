from neomodel import (
    StructuredNode,
    StructuredRel,
    StringProperty,
    IntegerProperty,
    BooleanProperty,
    DateProperty,
    DateTimeProperty,
    FloatProperty,
    JSONProperty,
    RelationshipTo,
    RelationshipFrom,
)
from finance.graph.models import Transaction


# -----------------------------
# Relationship Models (Edges)
# -----------------------------

class ShareholderRel(StructuredRel):
    shares_owned = IntegerProperty(required=True)
    date_joined = DateProperty()
    is_active = BooleanProperty(default=True)
    metadata = JSONProperty()
    created_at = DateTimeProperty(default_now=True)


class FundingRel(StructuredRel):
    amount = FloatProperty()
    currency = StringProperty()
    metadata = JSONProperty()
    created_at = DateTimeProperty(default_now=True)


class TransactionRel(StructuredRel):
    metadata = JSONProperty()
    created_at = DateTimeProperty(default_now=True)


# -----------------------------
# Node Models
# -----------------------------

class Company(StructuredNode):
    uid = StringProperty(unique_index=True, required=True)       # reuse SQL Company PK
    social_id = StringProperty(unique_index=True, required=True) # SocialEntity.id
    name = StringProperty(required=True, index=True)
    registration_number = StringProperty(unique_index=True)
    country = StringProperty()
    industry = StringProperty()
    date_founded = DateProperty()
    is_active = BooleanProperty(default=True)
    metadata = JSONProperty()

    # Relationships
    shareholders = RelationshipFrom('Shareholder', 'OWNS_SHARES', model=ShareholderRel)
    funding_rounds = RelationshipTo('FundingRound', 'RAISED_FUNDS', model=FundingRel)


class Shareholder(StructuredNode):
    uid = StringProperty(unique_index=True, required=True)
    social_id = StringProperty(unique_index=True, required=True)
    is_active = BooleanProperty(default=True)
    metadata = JSONProperty()

    # Relationships
    company = RelationshipTo('Company', 'OWNS_SHARES', model=ShareholderRel)


class FundingRound(StructuredNode):
    uid = StringProperty(unique_index=True, required=True)
    name = StringProperty(required=True)
    amount = FloatProperty()
    currency = StringProperty()
    timestamp = DateTimeProperty(default_now=True)
    metadata = JSONProperty()

    # Relationships
    venture = RelationshipFrom('Company', 'RAISED_FUNDS', model=FundingRel)
    transaction = RelationshipTo(Transaction, 'LINKED_TRANSACTION', model=TransactionRel)
