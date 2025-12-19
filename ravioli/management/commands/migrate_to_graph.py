import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.apps import apps
from django.conf import settings
from django.forms.models import model_to_dict
from django.db import transaction
from django.db.models.fields.related import ForeignKey, ManyToManyField

from ravioli.models import (
    Graph,
    CollectionType,
    DataNode,
    RelationType,
    DataEdge,
)
from django.core import serializers


class Command(BaseCommand):
    help = "Migrates existing Django models into the Graph/Node/Edge structure."

    @staticmethod
    def normalize_value(value):
        if isinstance(value, (datetime.date, datetime.datetime)):
            return value.isoformat()

        if isinstance(value, Decimal):
            return float(value)

        if isinstance(value, (list, tuple)):
            return [Command.normalize_value(v) for v in value]

        if isinstance(value, dict):
            return {k: Command.normalize_value(v) for k, v in value.items()}

        return value

    def handle(self, *args, **options):
        allowed_apps = getattr(settings, "GRAPH_ALLOWED_APPS", None)

        if not allowed_apps:
            self.stdout.write(self.style.ERROR(
                "GRAPH_ALLOWED_APPS is not defined or empty in settings.py"
            ))
            return

        self.stdout.write(self.style.SUCCESS(
            f"Migrating models from allowed apps: {', '.join(allowed_apps)}"
        ))

        graph, _ = Graph.objects.get_or_create(name="Main Graph")

        collection_map = {}
        node_map = {}

        with transaction.atomic():
            self.create_collection_types(collection_map, allowed_apps)
            self.create_nodes(collection_map, node_map, graph)
            self.create_edges(collection_map, node_map, graph)

        self.stdout.write(self.style.SUCCESS("Migration completed successfully!"))

    # ---------------------------------------------------------
    # STEP 1 — Create CollectionTypes for allowed models
    # ---------------------------------------------------------
    def create_collection_types(self, collection_map, allowed_apps):
        for model in apps.get_models():
            if model._meta.app_label not in allowed_apps:
                continue

            name = f"{model._meta.app_label}.{model.__name__}"
            ct, _ = CollectionType.objects.get_or_create(name=name)

            collection_map[model] = ct
            self.stdout.write(f"Created CollectionType: {name}")

    # ---------------------------------------------------------
    # STEP 2 — Create DataNodes for each instance
    # ---------------------------------------------------------
    def create_nodes(self, collection_map, node_map, graph):
        for model, collection_type in collection_map.items():
            for obj in model.objects.all():
                # Use Django serializer
                serialized = serializers.serialize("python", [obj])[0]

                # Extract only the fields dict
                fields = serialized["fields"]

                # Normalize everything to JSON-safe values
                normalized = self.normalize_value(fields)

                node = DataNode.objects.create(
                    name=f"{model.__name__}-{obj.pk}",
                    data=normalized,
                    collection_type=collection_type,
                    graph=graph,
                )

                node_map[(model, obj.pk)] = node

            self.stdout.write(f"Created nodes for {model.__name__}")

    # ---------------------------------------------------------
    # STEP 3 — Create DataEdges for FK and M2M
    # ---------------------------------------------------------
    def create_fk_edges(self, collection_map, node_map, graph):
        for model in collection_map.keys():
            for field in model._meta.get_fields():

                if not isinstance(field, ForeignKey):
                    continue

                # Skip FK to models outside GRAPH_ALLOWED_APPS
                if field.related_model not in collection_map:
                    continue

                rel_name = f"FK:{model.__name__}->{field.related_model.__name__}"
                relation_type, _ = RelationType.objects.get_or_create(name=rel_name)

                for obj in model.objects.all():
                    target_obj = getattr(obj, field.name, None)
                    if not target_obj:
                        continue

                    source_node = node_map[(model, obj.pk)]
                    target_node = node_map.get((field.related_model, target_obj.pk))

                    if not target_node:
                        continue

                    DataEdge.objects.get_or_create(
                        source=source_node,
                        target=target_node,
                        relation_type=relation_type,
                        graph=graph,
                    )

            self.stdout.write(f"Created FK edges for {model.__name__}")

    def create_m2m_edges(self, collection_map, node_map, graph):
        for model in collection_map.keys():
            for field in model._meta.get_fields():

                if not isinstance(field, ManyToManyField):
                    continue

                # Skip M2M to models outside GRAPH_ALLOWED_APPS
                if field.related_model not in collection_map:
                    continue

                rel_name = f"M2M:{model.__name__}->{field.related_model.__name__}"
                relation_type, _ = RelationType.objects.get_or_create(name=rel_name)

                for obj in model.objects.all():
                    source_node = node_map[(model, obj.pk)]

                    for target_obj in getattr(obj, field.name).all():
                        target_node = node_map.get((field.related_model, target_obj.pk))

                        if not target_node:
                            continue

                        DataEdge.objects.get_or_create(
                            source=source_node,
                            target=target_node,
                            relation_type=relation_type,
                            graph=graph,
                        )

            self.stdout.write(f"Created M2M edges for {model.__name__}")

    def create_edges(self, collection_map, node_map, graph):
        self.create_fk_edges(collection_map, node_map, graph)
        self.create_m2m_edges(collection_map, node_map, graph)

