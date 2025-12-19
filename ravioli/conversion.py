import json
import datetime
from decimal import Decimal

from django.apps import apps
from django.core import serializers

from .models import (
    Graph,
    CollectionType,
    RelationType,
    DataNode,
    DataEdge
)


class GraphConversionPipeline:
    """
    ETL pipeline using per-app Adapters.

    Steps:
      1. Extract Django model data for each app
      2. For each adapter:
            - build collection types
            - build relation types
            - build nodes
            - build edges
      3. Load nodes + edges into the graph
    """

    def __init__(self, adapters: list, graph: Graph = None):
        self.adapters = adapters
        self.graph = graph
        self.node_index = {}  # ETL id → DataNode instance

    # ---------------------------------------------------------
    # NORMALIZATION
    # ---------------------------------------------------------
    @staticmethod
    def normalize_value(value):
        if isinstance(value, (datetime.date, datetime.datetime)):
            return value.isoformat()

        if isinstance(value, Decimal):
            return float(value)

        if isinstance(value, (list, tuple)):
            return [GraphConversionPipeline.normalize_value(v) for v in value]

        if isinstance(value, dict):
            return {k: GraphConversionPipeline.normalize_value(v) for k, v in value.items()}

        return value

    # ---------------------------------------------------------
    # 1. EXTRACT PER APP
    # ---------------------------------------------------------
    def extract(self):
        serialized_data = {}

        for adapter in self.adapters:
            app_label = adapter.app_label
            app_config = apps.get_app_config(app_label)
            app_models = app_config.get_models()

            serialized_data[app_label] = {}

            for model in app_models:
                queryset = model.objects.all()
                serialized = serializers.serialize("python", queryset)
                normalized = self.normalize_value(serialized)
                serialized_data[app_label][model.__name__] = normalized

        return serialized_data

    # ---------------------------------------------------------
    # TYPE CREATION
    # ---------------------------------------------------------
    def ensure_collection_types(self, type_defs: list[dict]):
        for t in type_defs:
            CollectionType.objects.get_or_create(
                name=t["name"],
                defaults={
                    "json_schema": t.get("json_schema"),
                    "form_layout": t.get("form_layout"),
                }
            )

    def ensure_relation_types(self, type_defs: list[dict]):
        for t in type_defs:
            RelationType.objects.get_or_create(
                name=t["name"],
                defaults={"metadata": t.get("metadata")}
            )

    # ---------------------------------------------------------
    # 2. TRANSFORM USING ADAPTERS
    # ---------------------------------------------------------
    def transform(self, extracted_data):
        all_nodes = []
        all_edges = []

        for adapter in self.adapters:
            app_data = extracted_data.get(adapter.app_label, {})
            self.ensure_collection_types(adapter.build_collection_types())
            nodes = adapter.build_nodes(app_data)
            all_nodes.extend(nodes)

        for adapter in self.adapters:
            app_data = extracted_data.get(adapter.app_label, {})
            self.ensure_relation_types(adapter.build_relation_types())
            edges = adapter.build_edges(app_data)
            all_edges.extend(edges)
        return {"nodes": all_nodes, "edges": all_edges}

    # ---------------------------------------------------------
    # 3A. CREATE NODES
    # ---------------------------------------------------------
    def create_nodes(self, nodes: list[dict]):
        for node in nodes:
            type_name = node["type"]
            collection_type = CollectionType.objects.get(name=type_name)

            obj = DataNode.objects.create(
                name=node.get("name", node["id"]),
                data=node.get("data", {}),
                collection_type=collection_type,
                graph=self.graph
            )

            self.node_index[node["id"]] = obj

    # ---------------------------------------------------------
    # 3B. CREATE EDGES
    # ---------------------------------------------------------
    def create_edges(self, edges: list[dict]):
        for edge in edges:
            type_name = edge["type"]
            relation_type = RelationType.objects.get(name=type_name)

            DataEdge.objects.create(
                source=self.node_index[edge["source"]],
                target=self.node_index[edge["target"]],
                label=edge.get("label"),
                metadata=edge.get("metadata", {}),
                relation_type=relation_type,
                graph=self.graph
            )

    # ---------------------------------------------------------
    # 3. LOAD
    # ---------------------------------------------------------
    def load(self, transformed_data):
        if not self.graph:
            return transformed_data

        nodes = transformed_data["nodes"]
        edges = transformed_data["edges"]

        self.create_nodes(nodes)
        self.create_edges(edges)

        return {
            "nodes_created": len(nodes),
            "edges_created": len(edges)
        }

    # ---------------------------------------------------------
    # RUN ALL
    # ---------------------------------------------------------
    def run(self):
        extracted = self.extract()
        transformed = self.transform(extracted)
        loaded = self.load(transformed)
        return loaded
