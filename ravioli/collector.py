from abc import ABC, abstractmethod
from typing import List
from ravioli.models import Graph, CollectionType, RelationType, DataNode, DataEdge


class BaseAppCollector(ABC):
    """
    Abstract base for app-specific collectors.
    Provides static helpers for building collections, nodes, relation types, and edges.
    Subclasses override build_collections(), build_nodes(), build_relation_types(), build_edges().
    """

    def __init__(self, graph: Graph):
        self.graph = graph

    # ---- Hooks each app must implement ----
    @abstractmethod
    def build_collections(self) -> List[CollectionType]:
        """Return CollectionType instances that define node types."""
        pass

    @abstractmethod
    def build_nodes(self) -> List[DataNode]:
        """Return DataNode instances belonging to this graph."""
        pass

    @abstractmethod
    def build_relation_types(self) -> List[RelationType]:
        """Return RelationType instances that should exist in the graph."""
        pass

    @abstractmethod
    def build_edges(self) -> List[DataEdge]:
        """Return DataEdge instances linking nodes together."""
        pass

