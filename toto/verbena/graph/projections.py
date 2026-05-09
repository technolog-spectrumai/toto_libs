from toto.verbena.models import (
    Tag as TagSql,
    Page as PageSql,
    Topic as TopicSql,
    Section as SectionSql,
    Subsection as SubsectionSql,
    Book as BookSql,
    Chapter as ChapterSql,
)

from toto.verbena.graph.models import (
    TagNode,
    PageNode,
    TopicNode,
    SectionNode,
    SubsectionNode,
    BookNode,
    ChapterNode,
)

# External graph models (all DomainNode → use uid)
from toto.socialhub.graph.models import Person as PersonNode, Community as CommunityNode
from toto.events.graph.models import EventNode
from toto.locations.graph.models import (
    Address as AddressNode,
    Route as RouteNode,
    Territory as TerritoryNode,
)
from toto.core.graph.models import Federation


# =========================================================
# BASE PROJECTION
# =========================================================

class BaseVerbenaProjection:
    model = None
    app = "verbena"

    sql_model = None
    neo_model = None

    field_map = {}

    def item_count(self):
        return self.sql_model.objects.count()

    def link_count(self):
        return 0

    def node_data_size(self):
        return len(self.field_map)

    def projection_stats(self):
        return {
            "items": self.item_count(),
            "links": self.link_count(),
            "node_data_size": self.node_data_size(),
        }

    def sync_nodes(self):
        for obj in self.sql_model.objects.all():
            node = self.neo_model.nodes.get_or_none(uuid=str(obj.uid))

            if node and type(node) is not self.neo_model:
                node.delete()
                node = None

            if not node:
                node = self.neo_model(uuid=str(obj.uid))

            for neo_field, sql_field in self.field_map.items():
                setattr(node, neo_field, getattr(obj, sql_field))

            node.save()

    def sync_edges(self):
        pass


# =========================================================
# TAG PROJECTION
# =========================================================

class TagProjection(BaseVerbenaProjection):
    model = "Tag"
    sql_model = TagSql
    neo_model = TagNode

    field_map = {
        "name": "name",
        "slug": "slug",
    }


# =========================================================
# PAGE PROJECTION
# =========================================================

class PageProjection(BaseVerbenaProjection):
    model = "Page"
    sql_model = PageSql
    neo_model = PageNode

    field_map = {
        "title": "title",
        "slug": "slug",
        "description": "description",
        "created_at": "created_at",
    }

    def sync_edges(self):
        for p in PageSql.objects.all():
            gp = PageNode.nodes.get(uuid=str(p.uid))

            gp.tags.disconnect_all()
            for tag in p.tags.all():
                gt = TagNode.nodes.get_or_none(uuid=str(tag.uid))
                if gt:
                    gp.tags.connect(gt)

    def link_count(self):
        return sum(page.tags.count() for page in PageSql.objects.prefetch_related("tags"))


# =========================================================
# TOPIC PROJECTION
# =========================================================

class TopicProjection(BaseVerbenaProjection):
    model = "Topic"
    sql_model = TopicSql
    neo_model = TopicNode

    field_map = {
        "name": "name",
        "slug": "slug",
        "description": "description",
    }

    def sync_edges(self):
        for t in TopicSql.objects.all():
            gt = TopicNode.nodes.get(uuid=str(t.uid))

            gt.community.disconnect_all()
            gt.person.disconnect_all()
            gt.event.disconnect_all()
            gt.route.disconnect_all()
            gt.territory.disconnect_all()
            gt.address.disconnect_all()
            gt.federation.disconnect_all()

            if t.community_id:
                gc = CommunityNode.nodes.get_or_none(uuid=str(t.community.uid))
                if gc:
                    gt.community.connect(gc)

            if t.person_id:
                gp = PersonNode.nodes.get_or_none(uuid=str(t.person.uid))
                if gp:
                    gt.person.connect(gp)

            if t.event_id:
                ge = EventNode.nodes.get_or_none(uuid=str(t.event.uid))
                if ge:
                    gt.event.connect(ge)

            if t.route_id:
                gr = RouteNode.nodes.get_or_none(uuid=str(t.route.uid))
                if gr:
                    gt.route.connect(gr)

            if t.territory_id:
                gtr = TerritoryNode.nodes.get_or_none(uuid=str(t.territory.uid))
                if gtr:
                    gt.territory.connect(gtr)

            if t.address_id:
                ga = AddressNode.nodes.get_or_none(uuid=str(t.address.uid))
                if ga:
                    gt.address.connect(ga)

            if t.federation_id:
                gf = Federation.nodes.get_or_none(uuid=str(t.federation.uid))
                if gf:
                    gt.federation.connect(gf)

    def link_count(self):
        return (
            TopicSql.objects.filter(community__isnull=False).count()
            + TopicSql.objects.filter(person__isnull=False).count()
            + TopicSql.objects.filter(event__isnull=False).count()
            + TopicSql.objects.filter(route__isnull=False).count()
            + TopicSql.objects.filter(territory__isnull=False).count()
            + TopicSql.objects.filter(address__isnull=False).count()
            + TopicSql.objects.filter(federation__isnull=False).count()
        )


# =========================================================
# SECTION PROJECTION
# =========================================================

class SectionProjection(BaseVerbenaProjection):
    model = "Section"
    sql_model = SectionSql
    neo_model = SectionNode

    field_map = {
        "title": "title",
        "content": "content",
        "order": "order",
    }

    def sync_edges(self):
        for s in SectionSql.objects.all():
            gs = SectionNode.nodes.get(uuid=str(s.uid))

            gs.page.disconnect_all()
            gp = PageNode.nodes.get_or_none(uuid=str(s.page.uid))
            if gp:
                gs.page.connect(gp)

            gs.author.disconnect_all()
            if s.author_id:
                ga = PersonNode.nodes.get_or_none(uuid=str(s.author.uid))
                if ga:
                    gs.author.connect(ga)

            gs.tags.disconnect_all()
            for tag in s.tags.all():
                gt = TagNode.nodes.get_or_none(uuid=str(tag.uid))
                if gt:
                    gs.tags.connect(gt)

            gs.topics.disconnect_all()
            for topic in s.topics.all():
                gt = TopicNode.nodes.get_or_none(uuid=str(topic.uid))
                if gt:
                    gs.topics.connect(gt)

    def link_count(self):
        return (
            SectionSql.objects.count()
            + SectionSql.objects.filter(author__isnull=False).count()
            + sum(section.tags.count() for section in SectionSql.objects.prefetch_related("tags"))
            + sum(section.topics.count() for section in SectionSql.objects.prefetch_related("topics"))
        )


# =========================================================
# SUBSECTION PROJECTION
# =========================================================

class SubsectionProjection(BaseVerbenaProjection):
    model = "Subsection"
    sql_model = SubsectionSql
    neo_model = SubsectionNode

    field_map = {
        "title": "title",
        "content": "content",
        "order": "order",
    }

    def sync_edges(self):
        for ss in SubsectionSql.objects.all():
            gss = SubsectionNode.nodes.get(uuid=str(ss.uid))

            gss.section.disconnect_all()
            gs = SectionNode.nodes.get_or_none(uuid=str(ss.section.uid))
            if gs:
                gss.section.connect(gs)

            gss.topics.disconnect_all()
            for topic in ss.topics.all():
                gt = TopicNode.nodes.get_or_none(uuid=str(topic.uid))
                if gt:
                    gss.topics.connect(gt)

    def link_count(self):
        return (
            SubsectionSql.objects.count()
            + sum(subsection.topics.count() for subsection in SubsectionSql.objects.prefetch_related("topics"))
        )


# =========================================================
# BOOK PROJECTION
# =========================================================

class BookProjection(BaseVerbenaProjection):
    model = "Book"
    sql_model = BookSql
    neo_model = BookNode

    field_map = {
        "title": "title",
        "slug": "slug",
        "description": "description",
        "created_at": "created_at",
    }

    def sync_edges(self):
        for b in BookSql.objects.all():
            gb = BookNode.nodes.get(uuid=str(b.uid))

            gb.tags.disconnect_all()
            for tag in b.tags.all():
                gt = TagNode.nodes.get_or_none(uuid=str(tag.uid))
                if gt:
                    gb.tags.connect(gt)

    def link_count(self):
        return sum(book.tags.count() for book in BookSql.objects.prefetch_related("tags"))


# =========================================================
# CHAPTER PROJECTION
# =========================================================

class ChapterProjection(BaseVerbenaProjection):
    model = "Chapter"
    sql_model = ChapterSql
    neo_model = ChapterNode

    field_map = {
        "order": "order",
    }

    def sync_edges(self):
        for c in ChapterSql.objects.all():
            gc = ChapterNode.nodes.get(uuid=str(c.uid))

            gc.book.disconnect_all()
            gb = BookNode.nodes.get_or_none(uuid=str(c.book.uid))
            if gb:
                gc.book.connect(gb)

            gc.page.disconnect_all()
            gp = PageNode.nodes.get_or_none(uuid=str(c.page.uid))
            if gp:
                gc.page.connect(gp)

    def link_count(self):
        return ChapterSql.objects.count() * 2
