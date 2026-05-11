from neomodel import StringProperty, IntegerProperty, DateTimeProperty, RelationshipTo, RelationshipFrom
from toto.core.graph.models import DomainNode
from toto.socialhub.graph.models import Person as PersonNode

# ────────────────────────────────────────────────
# TAG
# ────────────────────────────────────────────────

class TagNode(DomainNode):
    __label__ = "Tag"
    name = StringProperty(required=True, unique_index=True)
    slug = StringProperty(index=True)
    pages = RelationshipFrom("PageNode", "HAS_TAG")
    sections = RelationshipFrom("SectionNode", "HAS_TAG")


# ────────────────────────────────────────────────
# PAGE
# ────────────────────────────────────────────────

class PageNode(DomainNode):
    __label__ = "Page"
    title = StringProperty(required=True)
    slug = StringProperty(index=True)
    description = StringProperty()
    created_at = DateTimeProperty()
    tags = RelationshipTo(TagNode, "HAS_TAG")
    sections = RelationshipFrom("SectionNode", "BELONGS_TO_PAGE")


# ────────────────────────────────────────────────
# SECTION
# ────────────────────────────────────────────────

class SectionNode(DomainNode):
    __label__ = "Section"
    title = StringProperty()
    content = StringProperty()
    order = IntegerProperty()
    page = RelationshipTo(PageNode, "BELONGS_TO_PAGE")
    author = RelationshipTo(PersonNode, "AUTHORED_BY")
    tags = RelationshipTo(TagNode, "HAS_TAG")
    subsections = RelationshipFrom("SubsectionNode", "BELONGS_TO_SECTION")


# ────────────────────────────────────────────────
# SUBSECTION
# ────────────────────────────────────────────────

class SubsectionNode(DomainNode):
    __label__ = "Subsection"
    title = StringProperty()
    content = StringProperty()
    order = IntegerProperty()
    section = RelationshipTo(SectionNode, "BELONGS_TO_SECTION")