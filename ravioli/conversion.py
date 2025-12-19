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
    Full ETL pipeline:
    1. Extract Django model data
    2. Transform using DataTransform model (Stage 2)
    3. Load into Graph as DataNode + DataEdge (Stage 3)
       with dynamic CollectionTypes and RelationTypes
    """

    def __init__(
        self,
        app_labels: list[str],
        transform_node=None,
        graph: Graph = None,
    ):
        self.app_labels = app_labels
        self.transform_node = transform_node
        self.graph = graph

        self.node_index = {}  # ETL id → DataNode instance


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
    # 1. EXTRACT
    # ---------------------------------------------------------
    def extract(self):
        serialized_data = {}
        for app_label in self.app_labels:
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
    # 2. TRANSFORM
    # ---------------------------------------------------------
    def transform(self, serialized_data):
        if not self.transform_node:
            return serialized_data

        return self.transform_node.run(serialized_data)

    # ---------------------------------------------------------
    # HELPERS: get or create types
    # ---------------------------------------------------------
    def get_collection_type(self, type_name: str) -> CollectionType:
        obj, _ = CollectionType.objects.get_or_create(name=type_name)
        return obj

    def get_relation_type(self, type_name: str) -> RelationType:
        obj, _ = RelationType.objects.get_or_create(name=type_name)
        return obj

    # ---------------------------------------------------------
    # 3A. CREATE NODES (dynamic types)
    # ---------------------------------------------------------
    def create_nodes(self, nodes: list[dict]):
        for node in nodes:
            type_name = node.get("type")
            if not type_name:
                raise ValueError(f"Node missing 'type': {node}")

            collection_type = self.get_collection_type(type_name)

            obj = DataNode.objects.create(
                name=node.get("name", node["id"]),
                data=node.get("data", {}),
                collection_type=collection_type,
                graph=self.graph
            )

            self.node_index[node["id"]] = obj

    # ---------------------------------------------------------
    # 3B. CREATE EDGES (dynamic types)
    # ---------------------------------------------------------
    def create_edges(self, edges: list[dict]):
        for edge in edges:
            type_name = edge.get("type")
            if not type_name:
                raise ValueError(f"Edge missing 'type': {edge}")

            relation_type = self.get_relation_type(type_name)

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
            return transformed_data  # nothing to load

        nodes = transformed_data.get("nodes", [])
        edges = transformed_data.get("edges", [])

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
        data1 = self.extract()
        print(json.dumps(data1, indent=2))
        data2 = self.transform(data1)
        data3 = self.load(data2)
        return data3
