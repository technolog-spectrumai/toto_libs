from ravioli.models import Note as SQLNote, Tag as SQLTag
from ravioli.graph.models import Note as GraphNote, Tag as GraphTag
from toto.neo4j import ConversionStrategy
from events.graph.models import Event as GraphEvent
from community.graph.models import Community as GraphCommunity, CommunityMember as GraphCommunityMember
from portfolio.graph.models import Company as GraphCompany


class NoteConversionStrategy(ConversionStrategy):
    def create(self, sql_obj: SQLNote):
        node = GraphNote(
            uid=str(sql_obj.pk),
            title=sql_obj.title,
            content=sql_obj.content,
            metadata=sql_obj.metadata,
            created_at=sql_obj.created_at,
            category=sql_obj.category,
        ).save()

        # connect tags
        for tag in sql_obj.tags.all():
            tag_node = GraphTag.nodes.get_or_none(uid=str(tag.pk))
            if not tag_node:
                tag_node = GraphTag(uid=str(tag.pk), name=tag.name).save()
            node.tags.connect(tag_node)

        # connect subject directly
        if sql_obj.subject:
            self._connect_subject(node, sql_obj.subject)

        return node

    def update(self, sql_obj: SQLNote, node):
        node.title = sql_obj.title
        node.content = sql_obj.content
        node.metadata = sql_obj.metadata
        node.created_at = sql_obj.created_at
        node.category = sql_obj.category
        node.save()

        # ensure tags
        for tag in sql_obj.tags.all():
            tag_node = GraphTag.nodes.get_or_none(uid=str(tag.pk))
            if not tag_node:
                tag_node = GraphTag(uid=str(tag.pk), name=tag.name).save()
            if not node.tags.is_connected(tag_node):
                node.tags.connect(tag_node)

        # ensure subject
        if sql_obj.subject:
            self._connect_subject(node, sql_obj.subject)

        return node

    def _connect_subject(self, node, subj):
        """Connect the note to the correct subject relationship."""
        mapping = {
            #"Event": (GraphEvent, "subject_event"),
            "Community": (GraphCommunity, "subject_community"),
            "CommunityMember": (GraphCommunityMember, "subject_member"),
            "Company": (GraphCompany, "subject_company"),
        }
        cls_name = subj.__class__.__name__
        graph_cls, rel_attr = mapping.get(cls_name, (None, None))
        if graph_cls:
            subj_node = graph_cls.nodes.get_or_none(uid=str(subj.pk))
            if not subj_node:
                subj_node = graph_cls(uid=str(subj.pk)).save()
            rel = getattr(node, rel_attr)
            if not rel.is_connected(subj_node):
                rel.connect(subj_node)

    def delete(self, sql_obj: SQLNote):
        node = self.get_node(sql_obj)
        if node:
            node.delete()

    def get_node(self, sql_obj: SQLNote):
        return GraphNote.nodes.get_or_none(uid=str(sql_obj.id))

    def get_all_nodes(self):
        return GraphNote.nodes.all()


class TagConversionStrategy(ConversionStrategy):
    def create(self, sql_obj: SQLTag):
        node = GraphTag(
            uid=str(sql_obj.pk),
            name=sql_obj.name,
            created_at=sql_obj.created_at,
        ).save()
        return node

    def update(self, sql_obj: SQLTag, node):
        node.name = sql_obj.name
        node.created_at = sql_obj.created_at
        node.save()
        return node

    def delete(self, sql_obj: SQLTag):
        node = self.get_node(sql_obj)
        if node:
            node.delete()

    def get_node(self, sql_obj: SQLTag):
        return GraphTag.nodes.get_or_none(uid=str(sql_obj.pk))

    def get_all_nodes(self):
        return GraphTag.nodes.all()
