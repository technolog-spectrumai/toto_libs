import datetime
from decimal import Decimal
from django.db.models.fields.related import ForeignKey, ManyToManyField
from django.core import serializers
from ravioli.models import (
    Graph,
    CollectionType,
    DataNode,
    RelationType,
    DataEdge,
)
from django.apps import apps


class CollectorHelper:
    """
    Instance-based helper for converting Django models into graph structures.
    Stores:
      - self.graph
      - self.node_lookup
    """

    def __init__(self, graph):
        self.graph = graph
        self.node_lookup = {}

    # ---------------------------------------------------------
    # STATIC HELPERS (pure functions)
    # ---------------------------------------------------------
    @staticmethod
    def normalize_value(value):
        if isinstance(value, (datetime.date, datetime.datetime)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, (list, tuple)):
            return [CollectorHelper.normalize_value(v) for v in value]
        if isinstance(value, dict):
            return {k: CollectorHelper.normalize_value(v) for k, v in value.items()}
        return value

    @staticmethod
    def create_collection_type(model):
        custom_name = getattr(model, "graph_node_type", None)
        name = custom_name or model.__name__
        ct, _ = CollectionType.objects.get_or_create(name=name)
        return ct

    def create_node(self, model, obj, collection_type):
        serialized = serializers.serialize("python", [obj])[0]
        fields = serialized["fields"]
        normalized = CollectorHelper.normalize_value(fields)

        node = DataNode.objects.create(
            name=f"{model.__name__}-{obj.pk}",
            data=normalized,
            collection_type=collection_type,
            graph=self.graph,
        )

        self.node_lookup[(model, obj.pk)] = node
        return node

    def create_fk_edges(self, model, obj, node):
        for field in model._meta.get_fields():
            if not isinstance(field, ForeignKey):
                continue

            target_obj = getattr(obj, field.name, None)
            if not target_obj:
                continue

            target_node = self.node_lookup.get((field.related_model, target_obj.pk))
            if not target_node:
                continue

            default_name = f"FK:{model.__name__}->{field.related_model.__name__}"
            rel_name = getattr(field, "db_comment", "") or default_name

            relation_type, _ = RelationType.objects.get_or_create(name=rel_name)

            DataEdge.objects.get_or_create(
                source=node,
                target=target_node,
                relation_type=relation_type,
                graph=self.graph,
            )

    def create_m2m_edges(self, model, obj, node):
        for field in model._meta.get_fields():
            if not isinstance(field, ManyToManyField):
                continue

            default_name = f"M2M:{model.__name__}->{field.related_model.__name__}"
            rel_name = getattr(field, "db_comment", "") or default_name

            relation_type, _ = RelationType.objects.get_or_create(name=rel_name)

            for target_obj in getattr(obj, field.name).all():
                target_node = self.node_lookup.get((field.related_model, target_obj.pk))
                if not target_node:
                    continue

                DataEdge.objects.get_or_create(
                    source=node,
                    target=target_node,
                    relation_type=relation_type,
                    graph=self.graph,
                )

    def get_node(self, model, pk):
        return self.node_lookup.get((model, pk))

    @staticmethod
    def build_graph(collectors, graph):
        """
        Run multiple collectors into a single graph.
        Uses one shared CollectorHelper instance.
        For each collector, uses its app_name to discover models.
        """

        if graph is None:
            raise ValueError("A graph instance is required.")

        helper = CollectorHelper(graph)

        for collector in collectors:
            app_label = collector.app_name

            # Get app config from app_label (e.g. "auth", "myapp")
            app_config = apps.get_app_config(app_label)

            # Iterate over all models in that app
            for model in app_config.get_models():

                # 1. Create collection type
                collection_type = CollectorHelper.create_collection_type(model)

                # 2. Create nodes
                for obj in model.objects.all():
                    helper.create_node(model, obj, collection_type)

                # 3. Create edges
                for obj in model.objects.all():
                    node = helper.get_node(model, obj.pk)
                    helper.create_fk_edges(model, obj, node)
                    helper.create_m2m_edges(model, obj, node)

        return helper