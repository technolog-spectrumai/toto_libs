from django.db import transaction
from .models import Graph, DataNode, DataEdge, CollectionType, RelationType


class MergeStrategy:
    """
    Defines how two graphs should be merged.
    Each strategy provides:
      - node_filter(a_nodes, b_nodes)
      - edge_filter(a_edges, b_edges, included_nodes)
    """

    # -------------------------
    # Strategy definitions
    # -------------------------

    INNER_JOIN = "inner_join"
    LEFT_JOIN = "left_join"
    RIGHT_JOIN = "right_join"
    FULL_OUTER_JOIN = "full_outer_join"
    UNION = "union"
    INTERSECTION = "intersection"
    A_MINUS_B = "a_minus_b"
    B_MINUS_A = "b_minus_a"

    # -------------------------
    # Node selection logic
    # -------------------------

    @staticmethod
    def select_nodes(strategy, a_nodes, b_nodes):
        """
        Returns a set of node names to include.
        Node identity = node.name
        """

        a_set = {n.name for n in a_nodes}
        b_set = {n.name for n in b_nodes}

        if strategy == MergeStrategy.INNER_JOIN:
            return a_set & b_set

        if strategy == MergeStrategy.LEFT_JOIN:
            return a_set | (a_set & b_set)

        if strategy == MergeStrategy.RIGHT_JOIN:
            return b_set | (a_set & b_set)

        if strategy == MergeStrategy.FULL_OUTER_JOIN:
            return a_set | b_set

        if strategy == MergeStrategy.UNION:
            return a_set | b_set

        if strategy == MergeStrategy.INTERSECTION:
            return a_set & b_set

        if strategy == MergeStrategy.A_MINUS_B:
            return a_set - b_set

        if strategy == MergeStrategy.B_MINUS_A:
            return b_set - a_set

        raise ValueError(f"Unknown merge strategy: {strategy}")

    # -------------------------
    # Edge selection logic
    # -------------------------

    @staticmethod
    def select_edges(strategy, a_edges, b_edges, included_node_names):
        """
        Returns edges whose source & target are included.
        """

        def valid(edge):
            return (
                edge.source.name in included_node_names
                and edge.target.name in included_node_names
            )

        if strategy in {
            MergeStrategy.INNER_JOIN,
            MergeStrategy.LEFT_JOIN,
            MergeStrategy.RIGHT_JOIN,
            MergeStrategy.FULL_OUTER_JOIN,
            MergeStrategy.UNION,
            MergeStrategy.INTERSECTION,
            MergeStrategy.A_MINUS_B,
            MergeStrategy.B_MINUS_A,
        }:
            # All strategies use the same rule:
            # include edges only if both nodes are included
            return [e for e in (list(a_edges) + list(b_edges)) if valid(e)]

        raise ValueError(f"Unknown merge strategy: {strategy}")


class MergeHelper:
    """
    Helper class to merge two graphs into a new graph using a merge strategy.
    """

    def __init__(self, strategy: str):
        if strategy not in {
            MergeStrategy.INNER_JOIN,
            MergeStrategy.LEFT_JOIN,
            MergeStrategy.RIGHT_JOIN,
            MergeStrategy.FULL_OUTER_JOIN,
            MergeStrategy.UNION,
            MergeStrategy.INTERSECTION,
            MergeStrategy.A_MINUS_B,
            MergeStrategy.B_MINUS_A,
        }:
            raise ValueError(f"Unknown merge strategy: {strategy}")

        self.strategy = strategy

    def merge(self, graph_a: Graph, graph_b: Graph, new_graph_name: str) -> Graph:
        """
        Create a new graph and merge nodes/edges from graph_a and graph_b
        according to the selected merge strategy.
        """

        with transaction.atomic():

            # 1. Create the new graph
            new_graph = Graph.objects.create(
                name=new_graph_name,
                description=f"Merged from {graph_a.name} and {graph_b.name} using {self.strategy}",
                created_by=graph_a.created_by,
            )

            # 2. Global type registries
            existing_ct = set(CollectionType.objects.values_list("id", flat=True))
            existing_rt = set(RelationType.objects.values_list("id", flat=True))

            # 3. Fetch nodes and edges
            a_nodes = list(DataNode.objects.filter(graph=graph_a))
            b_nodes = list(DataNode.objects.filter(graph=graph_b))

            a_edges = list(DataEdge.objects.filter(graph=graph_a))
            b_edges = list(DataEdge.objects.filter(graph=graph_b))

            # 4. Determine which node names to include
            included_node_names = MergeStrategy.select_nodes(
                self.strategy, a_nodes, b_nodes
            )

            # 5. Map old node IDs → new nodes
            node_map = {}

            # 6. Copy nodes
            MergeHelper._copy_nodes(a_nodes, included_node_names, existing_ct, new_graph, node_map)
            MergeHelper._copy_nodes(b_nodes, included_node_names, existing_ct, new_graph, node_map)

            # 7. Determine which edges to include
            included_edges = MergeStrategy.select_edges(
                self.strategy, a_edges, b_edges, included_node_names
            )

            # 8. Copy edges
            MergeHelper._copy_edges(included_edges, existing_rt, new_graph, node_map)

        return new_graph

    # ---------------------------------------------------------
    # INTERNAL STATIC HELPERS
    # ---------------------------------------------------------

    @staticmethod
    def _copy_nodes(nodes, included_names, existing_ct, new_graph, node_map):
        """
        Copy nodes whose names are included AND whose CollectionType exists.
        """

        for node in nodes:

            if node.name not in included_names:
                continue

            if node.collection_type_id not in existing_ct:
                continue  # skip nodes with missing type

            new_node, _ = DataNode.objects.get_or_create(
                name=node.name,
                graph=new_graph,
                defaults={
                    "data": node.data,
                    "collection_type": node.collection_type,
                }
            )

            node_map[node.id] = new_node

    @staticmethod
    def _copy_edges(edges, existing_rt, new_graph, node_map):
        """
        Copy edges whose type exists AND whose nodes were copied.
        """

        for edge in edges:

            if edge.relation_type_id not in existing_rt:
                continue

            if edge.source_id not in node_map:
                continue

            if edge.target_id not in node_map:
                continue

            DataEdge.objects.get_or_create(
                source=node_map[edge.source_id],
                target=node_map[edge.target_id],
                relation_type=edge.relation_type,
                graph=new_graph,
                defaults={"metadata": edge.metadata},
            )
