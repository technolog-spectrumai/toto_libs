from toto.bento.models import Category, IdeaBox, IdeaLink
from toto.bento.graph.models import CategoryNode, IdeaBoxNode


class CategoryProjection:
    model = "Category"
    app = "bento"

    def projection_stats(self):
        return {
            "items": Category.objects.count(),
            "links": 0,
            "node_data_size": 3,
        }

    def sync_nodes(self):
        for category in Category.objects.all():
            node = CategoryNode.nodes.get_or_none(uuid=str(category.uid))

            if not node:
                node = CategoryNode(
                    uuid=str(category.uid),
                    name=category.name,
                    slug=category.slug,
                    description=category.description,
                )
            else:
                node.name = category.name
                node.slug = category.slug
                node.description = category.description

            node.save()

    def sync_edges(self):
        pass


class IdeaBoxProjection:
    model = "IdeaBox"
    app = "bento"

    def projection_stats(self):
        return {
            "items": IdeaBox.objects.count(),
            "links": IdeaBox.objects.filter(category__isnull=False).count(),
            "node_data_size": 8,
        }

    def sync_nodes(self):
        for box in IdeaBox.objects.select_related("category").all():
            node = IdeaBoxNode.nodes.get_or_none(uuid=str(box.uid))

            if not node:
                node = IdeaBoxNode(
                    uuid=str(box.uid),
                    title=box.title,
                    body=box.body,
                    is_concept=box.is_concept,
                    source_title=box.source_title,
                    source_url=box.source_url,
                    source_type=box.source_type,
                    quote=box.quote,
                    properties=box.properties or {},
                    created_at=box.created_at,
                    updated_at=box.updated_at,
                )
            else:
                node.title = box.title
                node.body = box.body
                node.is_concept = box.is_concept
                node.source_title = box.source_title
                node.source_url = box.source_url
                node.source_type = box.source_type
                node.quote = box.quote
                node.properties = box.properties or {}
                node.created_at = box.created_at
                node.updated_at = box.updated_at

            node.save()

    def sync_edges(self):
        for box in IdeaBox.objects.all():
            node = IdeaBoxNode.nodes.get_or_none(uuid=str(box.uid))
            if node:
                node.category_node.disconnect_all()

        for box in IdeaBox.objects.select_related("category").all():
            box_node = IdeaBoxNode.nodes.get_or_none(uuid=str(box.uid))
            if not box_node:
                continue
            if box.category_id:
                category_node = CategoryNode.nodes.get_or_none(uuid=str(box.category.uid))
                if category_node:
                    box_node.category_node.connect(category_node)


class IdeaLinkProjection:
    model = "IdeaLink"
    app = "bento"

    def projection_stats(self):
        return {
            "items": 0,
            "links": IdeaLink.objects.count(),
            "node_data_size": 0,
        }

    def sync_nodes(self):
        pass

    def sync_edges(self):
        for box in IdeaBox.objects.all():
            node = IdeaBoxNode.nodes.get_or_none(uuid=str(box.uid))
            if node:
                node.links_to.disconnect_all()

        for link in IdeaLink.objects.select_related("from_box", "to_box").all():
            from_node = IdeaBoxNode.nodes.get_or_none(uuid=str(link.from_box.uid))
            to_node = IdeaBoxNode.nodes.get_or_none(uuid=str(link.to_box.uid))
            if not from_node or not to_node:
                continue
            from_node.links_to.connect(
                to_node,
                {
                    "label": link.label,
                    "properties": link.properties or {},
                    "created_at": link.created_at,
                },
            )