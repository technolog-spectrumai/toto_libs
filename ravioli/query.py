import json
from .models import CypherQuery
from toto.colors import ColorGenerator
from neo4j import GraphDatabase
from django.conf import settings



class GraphStyleResolver:
    """
    Resolves node and edge visual styles for a CypherQuery.
    Falls back to grey + default size if style is missing.
    """

    FALLBACK_NODE_COLOR = "#888888"
    FALLBACK_EDGE_COLOR = "#888888"
    FALLBACK_NODE_SIZE = 20
    FALLBACK_EDGE_SIZE = 2

    def __init__(self, cypher_query, palette="tab20"):
        self.generator = ColorGenerator(palette)

        self.node_styles = {
            ns.collection_type.name: ns
            for ns in cypher_query.node_styles.all()
        }

        self.edge_styles = {
            es.relation_type.name: es
            for es in cypher_query.edge_styles.all()
        }

    def node_color(self, label: str):
        style = self.node_styles.get(label)
        return style.color if style else self.FALLBACK_NODE_COLOR

    def node_size(self, label: str):
        style = self.node_styles.get(label)
        return style.size if style else self.FALLBACK_NODE_SIZE

    def edge_color(self, rel_type: str):
        style = self.edge_styles.get(rel_type)
        return style.color if style else self.FALLBACK_EDGE_COLOR

    def edge_size(self, rel_type: str):
        style = self.edge_styles.get(rel_type)
        return style.size if style else self.FALLBACK_EDGE_SIZE




def resolve_label(node):
    if getattr(node, "labels", None):
        labels = list(node.labels)
        if labels:
            return labels[0]
    return "Node"


def get_props(n):
    props = dict(n._properties)
    data = json.loads(props.get("data", "{}"))
    props.pop("data", None)
    props.update(data)
    return props


def run_cypher(query: str):
    driver = GraphDatabase.driver(
        f"bolt://{settings.NEO4J_HOST}:{settings.NEO4J_PORT}",
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
    )
    with driver.session() as session:
        result = session.run(query)
        records = list(result)
    driver.close()
    return records



class QueryHelper:
    """
    Loads a CypherQuery, runs it, applies styles,
    and returns graph JSON for the frontend.
    """

    def __init__(self, query_id):
        self.query_id = query_id
        self.query_obj = None
        self.cypher = None
        self.style = None

        self.elements = []
        self.seen_nodes = set()
        self.node_labels = set()
        self.edge_types = set()

        self._load_query()

    def _load_query(self):
        try:
            self.query_obj = CypherQuery.objects.get(pk=self.query_id, is_active=True)
            self.cypher = self.query_obj.query
            self.style = GraphStyleResolver(self.query_obj)
        except CypherQuery.DoesNotExist:
            self.cypher = "MATCH (n)-[r]->(m) RETURN n,r,m LIMIT 500"
            self.style = None

    def add_node(self, node):
        node_id = str(node.id)
        if node_id in self.seen_nodes:
            return

        self.seen_nodes.add(node_id)

        props = get_props(node)
        label = props.get("type", resolve_label(node))
        self.node_labels.add(label)

        self.elements.append({
            "data": {
                "id": node_id,
                "label": label,
                "color": self.style.node_color(label) if self.style else None,
                "size": self.style.node_size(label) if self.style else None,
                **props
            }
        })

    def add_edge(self, n, m, r):
        edge_type = r._properties.get("label", r.type)
        self.edge_types.add(edge_type)

        props = dict(r._properties)

        self.elements.append({
            "data": {
                "id": f"{n.id}-{m.id}",
                "source": str(n.id),
                "target": str(m.id),
                "type": edge_type,
                "color": self.style.edge_color(edge_type) if self.style else None,
                "size": self.style.edge_size(edge_type) if self.style else None,
                **props
            }
        })
    # ---------------------------------------------------------
    # Main entry point
    # ---------------------------------------------------------
    def run(self):
        results = run_cypher(self.cypher)

        for record in results:
            n, m, r = record["n"], record["m"], record["r"]
            self.add_node(n)
            self.add_node(m)
            self.add_edge(n, m, r)

        return {
            "elements": self.elements,
            "node_labels": list(self.node_labels),
            "edge_types": list(self.edge_types),
        }
