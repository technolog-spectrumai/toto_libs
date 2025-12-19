import datetime
from decimal import Decimal
from django.apps import apps
from django.conf import settings
from django.db import transaction
from django.db.models.fields.related import ForeignKey, ManyToManyField
from django.core import serializers

from ravioli.models import (
    Graph,
    CollectionType,
    DataNode,
    RelationType,
    DataEdge,
)


class GraphConverter:
    """
    Converts Django models into Graph / CollectionType / DataNode / DataEdge.
    Can be used anywhere in Python, not only as a Django management command.
    """

    def __init__(self, graph, allowed_apps=None):
        self.allowed_apps = allowed_apps or getattr(settings, "GRAPH_ALLOWED_APPS", None)

        if not self.allowed_apps:
            raise ValueError("GRAPH_ALLOWED_APPS is not defined or empty.")

        self.collection_map = {}
        self.node_map = {}
        self.graph = graph

    # ---------------------------------------------------------
    # Normalization helper
    # ---------------------------------------------------------
    @staticmethod
    def normalize_value(value):
        if isinstance(value, (datetime.date, datetime.datetime)):
            return value.isoformat()

        if isinstance(value, Decimal):
            return float(value)

        if isinstance(value, (list, tuple)):
            return [GraphConverter.normalize_value(v) for v in value]

        if isinstance(value, dict):
            return {k: GraphConverter.normalize_value(v) for k, v in value.items()}

        return value

    # ---------------------------------------------------------
    # Public entry point
    # ---------------------------------------------------------
    def migrate(self):
        print(f"Migrating models from allowed apps: {', '.join(self.allowed_apps)}")

        with transaction.atomic():
            self.create_collection_types()
            self.create_nodes()
            self.create_edges()

        print("Migration completed successfully!")

    # ---------------------------------------------------------
    # STEP 1 — Create CollectionTypes
    # ---------------------------------------------------------
    def create_collection_types(self):
        for model in apps.get_models():
            if model._meta.app_label not in self.allowed_apps:
                continue

            # Read custom node type name from Meta
            custom_name = getattr(model, "graph_node_type", None)

            if custom_name:
                name = custom_name
            else:
                # Default fallback
                name = f"{model._meta.app_label}.{model.__name__}"

            ct, _ = CollectionType.objects.get_or_create(name=name)

            self.collection_map[model] = ct
            print(f"Created CollectionType: {name}")

    # ---------------------------------------------------------
    # STEP 2 — Create DataNodes
    # ---------------------------------------------------------
    def create_nodes(self):
        for model, collection_type in self.collection_map.items():
            for obj in model.objects.all():
                serialized = serializers.serialize("python", [obj])[0]
                fields = serialized["fields"]
                normalized = self.normalize_value(fields)

                node = DataNode.objects.create(
                    name=f"{model.__name__}-{obj.pk}",
                    data=normalized,
                    collection_type=collection_type,
                    graph=self.graph,
                )

                self.node_map[(model, obj.pk)] = node

            print(f"Created nodes for {model.__name__}")

    # ---------------------------------------------------------
    # STEP 3 — Create DataEdges
    # ---------------------------------------------------------

    def get_relation_name(self, field, default_name):
        """
        Extract relation name from db_comment in Django 4.
        Example:
            db_comment="PLACED_BY"
        """
        comment = getattr(field, "db_comment", "") or ""

        # If db_comment is set, use it directly
        if comment:
            return comment.strip()

        return default_name

    def create_fk_edges(self):
        for model in self.collection_map.keys():
            for field in model._meta.get_fields():

                if not isinstance(field, ForeignKey):
                    continue

                if field.related_model not in self.collection_map:
                    continue

                default_name = f"FK:{model.__name__}->{field.related_model.__name__}"
                rel_name = self.get_relation_name(field, default_name)
                relation_type, _ = RelationType.objects.get_or_create(name=rel_name)

                for obj in model.objects.all():
                    target_obj = getattr(obj, field.name, None)
                    if not target_obj:
                        continue

                    source_node = self.node_map[(model, obj.pk)]
                    target_node = self.node_map.get((field.related_model, target_obj.pk))

                    if not target_node:
                        continue

                    DataEdge.objects.get_or_create(
                        source=source_node,
                        target=target_node,
                        relation_type=relation_type,
                        graph=self.graph,
                    )

            print(f"Created FK edges for {model.__name__}")

    def create_m2m_edges(self):
        for model in self.collection_map.keys():
            for field in model._meta.get_fields():

                if not isinstance(field, ManyToManyField):
                    continue

                if field.related_model not in self.collection_map:
                    continue

                default_name = f"M2M:{model.__name__}->{field.related_model.__name__}"
                rel_name = self.get_relation_name(field, default_name)
                relation_type, _ = RelationType.objects.get_or_create(name=rel_name)

                for obj in model.objects.all():
                    source_node = self.node_map[(model, obj.pk)]

                    for target_obj in getattr(obj, field.name).all():
                        target_node = self.node_map.get((field.related_model, target_obj.pk))

                        if not target_node:
                            continue

                        DataEdge.objects.get_or_create(
                            source=source_node,
                            target=target_node,
                            relation_type=relation_type,
                            graph=self.graph,
                        )

            print(f"Created M2M edges for {model.__name__}")

    def create_edges(self):
        self.create_fk_edges()
        self.create_m2m_edges()
