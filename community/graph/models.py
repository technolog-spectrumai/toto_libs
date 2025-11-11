from neomodel import (
    StructuredNode,
    StructuredRel,
    StringProperty,
    JSONProperty,
    DateTimeProperty,
    RelationshipTo,
    RelationshipFrom,
)
from federal.graph.models import Federation

# -----------------------------
# Relationship Models (Edges)
# -----------------------------

class MembershipRel(StructuredRel):
    role = StringProperty()              # e.g., "member", "head"
    metadata = JSONProperty()
    created_at = DateTimeProperty(default_now=True)


class PatronRel(StructuredRel):
    metadata = JSONProperty()
    created_at = DateTimeProperty(default_now=True)


class AffiliationRel(StructuredRel):
    role = StringProperty()              # e.g., "affiliate", "founder", "partner"
    metadata = JSONProperty()
    created_at = DateTimeProperty(default_now=True)


# -----------------------------
# Node Models
# -----------------------------

class Community(StructuredNode):
    uid = StringProperty(unique_index=True, required=True)       # reuse SQL Community PK
    social_id = StringProperty(unique_index=True, required=True) # SocialEntity.id
    name = StringProperty(required=True, index=True)
    established_year = StringProperty()
    metadata = JSONProperty()
    created_at = DateTimeProperty(default_now=True)

    # Relationships
    members = RelationshipFrom('CommunityMember', 'MEMBER_OF', model=MembershipRel)
    federation = RelationshipTo(Federation, 'AFFILIATED_WITH', model=AffiliationRel)


class CommunityMember(StructuredNode):
    uid = StringProperty(unique_index=True, required=True)       # reuse SQL CommunityMember PK
    social_id = StringProperty(unique_index=True, required=True) # SocialEntity.id
    display_name = StringProperty(required=True, index=True)
    bio = StringProperty()
    metadata = JSONProperty()
    joined_date = DateTimeProperty(default_now=True)

    # Relationships
    communities = RelationshipTo('Community', 'MEMBER_OF', model=MembershipRel)
    patron = RelationshipTo('CommunityMember', 'MENTORED_BY', model=PatronRel)
