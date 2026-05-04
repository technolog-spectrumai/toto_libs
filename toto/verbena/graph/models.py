from neomodel import (
    StringProperty,
    IntegerProperty,
    DateTimeProperty,
    RelationshipTo,
    RelationshipFrom,
)
from toto.core.domain import DomainNode

# External graph models
from toto.socialhub.graph.models import Person as PersonNode
from toto.socialhub.graph.models import Community as CommunityNode
from toto.events.graph.models import EventNode
from toto.locations.graph.models import Address as AddressNode
from toto.locations.graph.models import Route as RouteNode
from toto.locations.graph.models import Territory as TerritoryNode
from toto.core.graph.models import Federation


# ────────────────────────────────────────────────
# TAG
# ────────────────────────────────────────────────

class TagNode(DomainNode):
    __label__ = "Tag"

    name = StringProperty(required=True, unique_index=True)
    slug = StringProperty(index=True)

    pages = RelationshipFrom("PageNode", "HAS_TAG")
    sections = RelationshipFrom("SectionNode", "HAS_TAG")
    books = RelationshipFrom("BookNode", "HAS_TAG")


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
    chapters = RelationshipFrom("ChapterNode", "PAGE_IN_CHAPTER")


# ────────────────────────────────────────────────
# TOPIC
# ────────────────────────────────────────────────

class TopicNode(DomainNode):
    __label__ = "Topic"

    name = StringProperty(required=True)
    slug = StringProperty(index=True)
    description = StringProperty()

    # Optional domain links — unified to ABOUT
    community = RelationshipTo(CommunityNode, "ABOUT")
    person = RelationshipTo(PersonNode, "ABOUT")
    event = RelationshipTo(EventNode, "ABOUT")
    route = RelationshipTo(RouteNode, "ABOUT")
    territory = RelationshipTo(TerritoryNode, "ABOUT")
    address = RelationshipTo(AddressNode, "ABOUT")
    federation = RelationshipTo(Federation, "ABOUT")

    sections = RelationshipFrom("SectionNode", "HAS_TOPIC")
    subsections = RelationshipFrom("SubsectionNode", "HAS_TOPIC")


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
    topics = RelationshipTo(TopicNode, "HAS_TOPIC")
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
    topics = RelationshipTo(TopicNode, "HAS_TOPIC")


# ────────────────────────────────────────────────
# BOOK
# ────────────────────────────────────────────────

class BookNode(DomainNode):
    __label__ = "Book"

    title = StringProperty(required=True)
    slug = StringProperty(index=True)
    description = StringProperty()
    created_at = DateTimeProperty()

    tags = RelationshipTo(TagNode, "HAS_TAG")
    chapters = RelationshipFrom("ChapterNode", "CHAPTER_OF_BOOK")


# ────────────────────────────────────────────────
# CHAPTER
# ────────────────────────────────────────────────

class ChapterNode(DomainNode):
    __label__ = "Chapter"

    order = IntegerProperty()

    book = RelationshipTo(BookNode, "CHAPTER_OF_BOOK")
    page = RelationshipTo(PageNode, "PAGE_IN_CHAPTER")
