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

        self.serialized_data = {}
        self.transformed_data = None
        self.node_index = {}  # ETL id → DataNode instance

    # ---------------------------------------------------------
    # 1. EXTRACT
    # ---------------------------------------------------------
    def extract(self):
        for app_label in self.app_labels:
            app_config = apps.get_app_config(app_label)
            app_models = app_config.get_models()

            self.serialized_data[app_label] = {}

            for model in app_models:
                queryset = model.objects.all()
                serialized = serializers.serialize("python", queryset)
                self.serialized_data[app_label][model.__name__] = serialized

        return self.serialized_data

    # ---------------------------------------------------------
    # 2. TRANSFORM
    # ---------------------------------------------------------
    def transform(self):
        if not self.transform_node:
            self.transformed_data = self.serialized_data
            return self.transformed_data

        self.transformed_data = self.transform_node.run(self.serialized_data)
        return self.transformed_data

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
    def load(self):
        if not self.graph:
            return self.transformed_data  # nothing to load

        nodes = self.transformed_data.get("nodes", [])
        edges = self.transformed_data.get("edges", [])

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
        self.extract()
        self.transform()
        return self.load()
