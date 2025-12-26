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
        app_label = model._meta.app_label
        model_name = f"{app_label}.{model.__name__}"
        ct, _ = CollectionType.objects.get_or_create(name=name, model_name=model_name)
        return ct

    @staticmethod
    def create_relation_type(model, field, prefix):
        default_name = f"{prefix}:{model.__name__}->{field.related_model.__name__}"
        rel_name = getattr(field, "db_comment", "") or default_name
        relation_type, _ = RelationType.objects.get_or_create(name=rel_name)
        return relation_type

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

            # Build relation name
            default_name = f"FK:{model.__name__}->{field.related_model.__name__}"
            rel_name = getattr(field, "db_comment", "") or default_name

            # Lookup only — do NOT create
            relation_type = RelationType.objects.filter(name=rel_name).first()
            if not relation_type:
                continue  # skip missing relation types

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

            # Build relation name
            default_name = f"M2M:{model.__name__}->{field.related_model.__name__}"
            rel_name = getattr(field, "db_comment", "") or default_name

            # Lookup only — do NOT create
            relation_type = RelationType.objects.filter(name=rel_name).first()
            if not relation_type:
                continue  # skip missing relation types

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
    def build_types(collector):
        app_label = collector.app_name
        app_config = apps.get_app_config(app_label)

        for model in app_config.get_models():

            # 1. Create CollectionType
            ct = CollectorHelper.create_collection_type(model)

            # 2. Create RelationTypes for FK + M2M
            for field in model._meta.get_fields():

                # ForeignKey
                if isinstance(field, ForeignKey):
                    CollectorHelper.create_relation_type(model, field, prefix="FK")

                # ManyToMany
                if isinstance(field, ManyToManyField):
                    CollectorHelper.create_relation_type(model, field, prefix="M2M")


    @staticmethod
    def build_graph(collectors, graph):
        """
        Build graph using previously created CollectionTypes.
        If a model has no CollectionType, skip it.
        """

        if graph is None:
            raise ValueError("A graph instance is required.")

        helper = CollectorHelper(graph)

        for collector in collectors:
            app_label = collector.app_name
            app_config = apps.get_app_config(app_label)

            for model in app_config.get_models():

                model_path = f"{model._meta.app_label}.{model.__name__}"

                # Look up type fresh from DB
                collection_type = CollectionType.objects.filter(
                    model_name=model_path
                ).first()

                if not collection_type:
                    # Skip models without types
                    continue

                # Create nodes
                for obj in model.objects.all():
                    helper.create_node(model, obj, collection_type)

                # Create edges
                for obj in model.objects.all():
                    node = helper.get_node(model, obj.pk)
                    helper.create_fk_edges(model, obj, node)
                    helper.create_m2m_edges(model, obj, node)

        return helper

