from neomodel import (
    StringProperty,
    BooleanProperty,
    DateTimeProperty,
    JSONProperty,
    RelationshipTo,
    RelationshipFrom,
    StructuredRel,
)

from toto.core.graph.base import DomainNode


class IdeaLinkRel(StructuredRel):
    label = StringProperty()
    properties = JSONProperty(default={})
    created_at = DateTimeProperty()


class CategoryNode(DomainNode):
    __label__ = "IdeaCategory"

    name = StringProperty(required=True)
    slug = StringProperty(index=True)
    description = StringProperty()

    idea_boxes = RelationshipFrom("IdeaBoxNode", "HAS_CATEGORY")


class IdeaBoxNode(DomainNode):
    __label__ = "IdeaBox"

    title = StringProperty()
    body = StringProperty()

    is_concept = BooleanProperty(default=False)

    source_title = StringProperty()
    source_url = StringProperty()
    source_type = StringProperty()

    quote = StringProperty()

    properties = JSONProperty(default={})

    created_at = DateTimeProperty()
    updated_at = DateTimeProperty()

    category_node = RelationshipTo(CategoryNode, "HAS_CATEGORY")

    links_to = RelationshipTo(
        "IdeaBoxNode",
        "IDEA_LINK",
        model=IdeaLinkRel,
    )

    links_from = RelationshipFrom(
        "IdeaBoxNode",
        "IDEA_LINK",
        model=IdeaLinkRel,
    )