from ravioli.models import CollectionType, RelationType
from toto.colors import ColorGenerator
from .models import NodeStyle, EdgeStyle


class StyleFactory:
    """
    Creates missing NodeStyle and EdgeStyle entries for a given CypherQuery.
    Uses deterministic color generation based on index.
    """

    def __init__(self):
        self.node_colors = ColorGenerator("tab20")
        self.edge_colors = ColorGenerator("tab20c")

    # ---------------------------------------------------------
    # Public API
    # ---------------------------------------------------------

    def create_missing_styles(self, cypher_query) -> int:
        """
        Creates missing node + edge styles for a CypherQuery.
        Returns number of created style objects.
        """
        created = 0
        created += self._create_node_styles(cypher_query)
        created += self._create_edge_styles(cypher_query)
        return created

    # ---------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------

    def _create_node_styles(self, cypher_query) -> int:
        created = 0

        for idx, ct in enumerate(CollectionType.objects.all()):
            _, was_created = NodeStyle.objects.get_or_create(
                cypher_query=cypher_query,
                collection_type=ct,
                defaults={
                    "color": self.node_colors.color_for_id(idx),
                    "size": 20,
                }
            )
            if was_created:
                created += 1

        return created

    def _create_edge_styles(self, cypher_query) -> int:
        created = 0

        for idx, rt in enumerate(RelationType.objects.all()):
            _, was_created = EdgeStyle.objects.get_or_create(
                cypher_query=cypher_query,
                relation_type=rt,
                defaults={
                    "color": self.edge_colors.color_for_id(idx),
                    "size": 2,
                }
            )
            if was_created:
                created += 1

        return created
