from neomodel import (
    StructuredNode,
    StructuredRel,
    StringProperty,
    JSONProperty,
    DateTimeProperty,
    RelationshipTo,
    RelationshipFrom,
)
from federal.graph.models import Federation, FederatedIdentity


class MembershipRel(StructuredRel):
    role = StringProperty()
    metadata = JSONProperty()
    created_at = DateTimeProperty(default_now=True)


class PatronRel(StructuredRel):
    metadata = JSONProperty()
    created_at = DateTimeProperty(default_now=True)


class AffiliationRel(StructuredRel):
    role = StringProperty()
    metadata = JSONProperty()
    created_at = DateTimeProperty(default_now=True)


class Community(StructuredNode):
    uid = StringProperty(unique_index=True, required=True)
    name = StringProperty(required=True, index=True)
    established_year = StringProperty()
    metadata = JSONProperty()
    created_at = DateTimeProperty(default_now=True)

    members = RelationshipFrom('CommunityMember', 'MEMBER_OF', model=MembershipRel)
    federation = RelationshipTo(Federation, 'AFFILIATED_WITH', model=AffiliationRel)


class CommunityMember(StructuredNode):
    uid = StringProperty(unique_index=True, required=True)
    display_name = StringProperty(required=True, index=True)
    bio = StringProperty()
    metadata = JSONProperty()
    joined_date = DateTimeProperty(default_now=True)
    user_id = StringProperty(index=True, required=False)   # ✅ optional SQL User PK

    communities = RelationshipTo('Community', 'MEMBER_OF', model=MembershipRel)
    patron = RelationshipTo('CommunityMember', 'MENTORED_BY', model=PatronRel)
    identity = RelationshipTo(FederatedIdentity, 'LINKED_IDENTITY')
