from neomodel import (
    StructuredNode, StringProperty, IntegerProperty,
    BooleanProperty, DateProperty, RelationshipTo, RelationshipFrom
)


class DomainNode(StructuredNode):
    __abstract_node__ = True
    uuid = StringProperty(
        unique_index=True,
        required=True
    )


class Community(DomainNode):
    __label__ = "Community"
    name = StringProperty(required=True)
    slug = StringProperty(index=True)
    org_type = StringProperty()
    established_year = IntegerProperty()
    is_autonomous = BooleanProperty(default=False)
    is_foreign = BooleanProperty(default=False)

    members = RelationshipTo("CommunityMember", "HAS_MEMBER")
    head = RelationshipTo("CommunityMember", "HEAD")


class CommunityMember(DomainNode):
    __label__ = "CommunityMember"
    display_name = StringProperty(required=True)
    email = StringProperty()
    phone = StringProperty()
    date_of_birth = DateProperty()

    communities = RelationshipFrom("Community", "HAS_MEMBER")
    patron = RelationshipTo("CommunityMember", "MENTORED_BY")
