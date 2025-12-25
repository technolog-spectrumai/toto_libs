import json
import networkx as nx
from neo4j import GraphDatabase
from django.conf import settings
from .models import CypherQuery
from toto.colors import ColorGenerator
from toto.executor import RestrictedPythonExecutor


class GraphStyleResolver:
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

    def node_color(self, label):
        style = self.node_styles.get(label)
        return style.color if style else self.FALLBACK_NODE_COLOR

    def node_size(self, label):
        style = self.node_styles.get(label)
        return style.size if style else self.FALLBACK_NODE_SIZE

    def edge_color(self, rel_type):
        style = self.edge_styles.get(rel_type)
        return style.color if style else self.FALLBACK_EDGE_COLOR

    def edge_size(self, rel_type):
        style = self.edge_styles.get(rel_type)
        return style.size if style else self.FALLBACK_EDGE_SIZE


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

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


# ---------------------------------------------------------
# NetworkX conversion
# ---------------------------------------------------------

def records_to_networkx(records):
    G = nx.DiGraph()

    for record in records:
        n, m, r = record["n"], record["m"], record["r"]

        # Node n
        props_n = get_props(n)
        label_n = props_n.get("type", resolve_label(n))
        G.add_node(str(n.id), label=label_n, **props_n)

        # Node m
        props_m = get_props(m)
        label_m = props_m.get("type", resolve_label(m))
        G.add_node(str(m.id), label=label_m, **props_m)

        # Edge r
        edge_type = r._properties.get("label", r.type)
        props_r = dict(r._properties)
        G.add_edge(str(n.id), str(m.id), type=edge_type, **props_r)

    return G

def apply_user_lambda(cypher_query, graph):
    """
    Executes restricted Python code stored in cypher_query.code.
    The code must define:  def main(G): return G
    """
    if not cypher_query.code:
        return graph

    executor = RestrictedPythonExecutor(
        code=cypher_query.code,
        context={"G": graph},
        name=f"cypher_query_{cypher_query.id}"
    )

    result = executor.execute(extra_globals={"nx": nx})

    # If RestrictedPython returns an error, keep original graph
    if isinstance(result, dict) and "error" in result:
        print("RestrictedPython error:", result["error"])
        return graph

    # If user returned nothing, keep original graph
    if result is None:
        return graph

    return result



def networkx_to_elements(G, style):
    elements = []
    node_labels = set()
    edge_types = set()

    # Nodes
    for node_id, data in G.nodes(data=True):
        label = data.get("label", "Node")
        node_labels.add(label)

        elements.append({
            "data": {
                "id": node_id,
                "label": label,
                "color": style.node_color(label) if style else None,
                "size": style.node_size(label) if style else None,
                **data
            }
        })

    # Edges
    for u, v, data in G.edges(data=True):
        edge_type = data.get("type", "REL")
        edge_types.add(edge_type)

        elements.append({
            "data": {
                "id": f"{u}-{v}",
                "source": u,
                "target": v,
                "type": edge_type,
                "color": style.edge_color(edge_type) if style else None,
                "size": style.edge_size(edge_type) if style else None,
                **data
            }
        })

    return elements, node_labels, edge_types


# ---------------------------------------------------------
# Main QueryHelper
# ---------------------------------------------------------

class QueryHelper:
    """
    Loads a CypherQuery, runs it, converts to NetworkX,
    applies user lambda, converts back, applies styles,
    and returns graph JSON for the frontend.
    """

    def __init__(self, query_id):
        self.query_id = query_id
        self.query_obj = None
        self.cypher = None
        self.style = None

        self._load_query()

    def _load_query(self):
        try:
            self.query_obj = CypherQuery.objects.get(pk=self.query_id, is_active=True)
            self.cypher = self.query_obj.query
            self.style = GraphStyleResolver(self.query_obj)
        except CypherQuery.DoesNotExist:
            self.cypher = "MATCH (n)-[r]->(m) RETURN n,r,m LIMIT 500"
            self.style = None

    def run(self):
        # Step 1: run cypher
        records = run_cypher(self.cypher)

        # Step 2: convert to networkx
        G = records_to_networkx(records)

        # Step 3: apply user lambda
        G = apply_user_lambda(self.query_obj, G)

        # Step 4: convert back to cytoscape JSON
        elements, node_labels, edge_types = networkx_to_elements(G, self.style)

        return {
            "elements": elements,
            "node_labels": list(node_labels),
            "edge_types": list(edge_types),
        }
