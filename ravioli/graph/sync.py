from ravioli.models import Note as SQLNote, Tag as SQLTag
from ravioli.graph.models import Note as GraphNote, Tag as GraphTag
from toto.neo4j import ConversionStrategy


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

        # connect related notes
        for related in sql_obj.related.all():
            related_node = GraphNote.nodes.get_or_none(uid=str(related.pk))
            if related_node and not node.related.is_connected(related_node):
                node.related.connect(related_node)

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

        # ensure related notes
        for related in sql_obj.related.all():
            related_node = GraphNote.nodes.get_or_none(uid=str(related.pk))
            if related_node and not node.related.is_connected(related_node):
                node.related.connect(related_node)

        return node

    def delete(self, sql_obj: SQLNote):
        node = self.get_node(sql_obj)
        if node:
            node.delete()

    def get_node(self, sql_obj: SQLNote):
        return GraphNote.nodes.get_or_none(uid=str(sql_obj.pk))

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
