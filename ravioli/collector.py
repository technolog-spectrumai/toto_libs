from abc import ABC, abstractmethod
from django.db import models
from ravioli.models import Graph, CollectionType, RelationType, DataNode, DataEdge


class BaseAppCollector(ABC):
    """
    Abstract base for app-specific collectors.
    Provides static helpers for building collections, nodes, relations, and edges.
    Subclasses override build_collections(), build_nodes(), build_relations().
    """

    def __init__(self, graph: Graph):
        self.graph = graph

    # ---- Hooks each app must implement ----
    @abstractmethod
    def build_collections(self) -> list[type[models.Model]]:
        """Return model classes that should become CollectionTypes."""
        pass

    @abstractmethod
    def build_nodes(self) -> list[models.Model]:
        """Return model instances that should become DataNodes."""
        pass

    @abstractmethod
    def build_relations(self) -> list[tuple]:
        """
        Return relations as tuples:
        (source_instance, target_instance, relation_name, metadata_dict)
        """
        pass

    # ---- Static helpers ----
    @staticmethod
    def create_collection(model_cls: type[models.Model]) -> CollectionType:
        props = {
            f.name: {"type": f.get_internal_type().lower()}
            for f in model_cls._meta.fields if not f.is_relation
        }
        collection, _ = CollectionType.objects.get_or_create(
            name=model_cls.__name__,
            defaults={"json_schema": {"properties": props}, "form_layout": {"layout": "auto"}}
        )
        return collection

    @staticmethod
    def create_node(instance: models.Model, graph: Graph, collection: CollectionType) -> DataNode:
        data = {f.name: getattr(instance, f.name)
                for f in instance._meta.fields if not f.is_relation}
        node, _ = DataNode.objects.get_or_create(
            name=f"{instance.__class__.__name__}-{instance.pk}",
            graph=graph,
            collection_type=collection,
            defaults={"data": data}
        )
        return node

    @staticmethod
    def create_relation(name: str) -> RelationType:
        relation, _ = RelationType.objects.get_or_create(
            name=name,
            defaults={"json_schema": {"relation": "unspecified"}, "form_layout": {"layout": "auto"}}
        )
        return relation

    @staticmethod
    def create_edge(source: DataNode, target: DataNode, graph: Graph,
                    relation: RelationType, label: str = None, metadata: dict = None) -> DataEdge:
        edge, _ = DataEdge.objects.get_or_create(
            source=source,
            target=target,
            relation_type=relation,
            graph=graph,
            label=label,
            defaults={"metadata": metadata or {}}
        )
        return edge

    # ---- Orchestration ----
    def run(self):
        # Build collections
        collections = {cls.__name__: self.create_collection(cls) for cls in self.build_collections()}

        # Build nodes
        nodes = {}
        for inst in self.build_nodes():
            ct = collections[inst.__class__.__name__]
            nodes[inst.pk] = self.create_node(inst, self.graph, ct)

        # Build relations/edges
        for src, tgt, rel_name, meta in self.build_relations():
            relation = self.create_relation(rel_name)
            self.create_edge(nodes[src.pk], nodes[tgt.pk], self.graph, relation,
                             label=rel_name, metadata=meta)
