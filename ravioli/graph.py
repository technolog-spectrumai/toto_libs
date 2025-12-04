# translators.py
from neomodel import db
from .neo_models import NeoDataNode


class GraphTranslator:
    """
    Responsible for converting a Django Graph (with DataNodes and DataEdges)
    into Neo4j using neomodel.
    """

    def __init__(self, graph):
        self.graph = graph
        self.node_map = {}

    def clear_existing(self):
        """
        Remove any existing Neo4j nodes tagged with this graph name.
        """
        db.cypher_query(
            "MATCH (n {graph_name:$graph}) DETACH DELETE n",
            {"graph": self.graph.name}
        )

    def export_nodes(self):
        """
        Create NeoDataNode objects for each DataNode in the graph.
        """
        for node in self.graph.nodes.all():
            neo_node = NeoDataNode(
                name=node.name,
                type=node.collection_type.name,
                data=node.data or {},
                created_at=node.created_at
            ).save()
            # tag with graph name for scoping
            neo_node._set_properties({"graph_name": self.graph.name})
            neo_node.save()
            self.node_map[node.id] = neo_node

    def export_edges(self):
        """
        Create relationships between nodes for each DataEdge in the graph.
        """
        for edge in self.graph.edges.all():
            source = self.node_map.get(edge.source_id)
            target = self.node_map.get(edge.target_id)
            if source and target:
                rel = source.relates_to.connect(target, {
                    "relation_type": edge.relation_type.name,
                    "label": edge.label,
                    "metadata": edge.metadata or {},
                    "created_at": edge.created_at,
                })
                rel.save()

    def export(self):
        """
        Full export routine: clear, then push nodes and edges.
        """
        self.clear_existing()
        self.export_nodes()
        self.export_edges()
        return f"Graph '{self.graph.name}' exported to Neo4j with {len(self.node_map)} nodes and {self.graph.edges.count()} edges."
