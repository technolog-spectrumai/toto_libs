from toto.verbena.models import (
    Tag as TagSql,
    Page as PageSql,
    Section as SectionSql,
)

from toto.verbena.graph.models import (
    TagNode,
    PageNode,
    SectionNode,
    SubsectionNode,
)

from toto.socialhub.graph.models import Person as PersonNode

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
    field_map = {"name": "name", "slug": "slug"}


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
# SECTION PROJECTION
# =========================================================

class SectionProjection(BaseVerbenaProjection):
    model = "Section"
    sql_model = SectionSql
    neo_model = SectionNode
    field_map = {"title": "title", "content": "content", "order": "order"}

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

    def link_count(self):
        return (
            SectionSql.objects.count()
            + SectionSql.objects.filter(author__isnull=False).count()
            + sum(section.tags.count() for section in SectionSql.objects.prefetch_related("tags"))
        )


