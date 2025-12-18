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

    @abstractmethod
    def build_nodes(self) -> List[DataNode]:
        """Return DataNode instances belonging to this graph."""
        pass

    @abstractmethod
    def build_edges(self) -> List[DataEdge]:
        """Return DataEdge instances linking nodes together."""
        pass

