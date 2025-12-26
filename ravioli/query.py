import json
import networkx as nx
from neo4j import GraphDatabase
from django.conf import settings
from toto.executor import RestrictedPythonExecutor
from toto.colors import ColorGenerator


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


class CypherQueryHelper:
    """
    Pure static helper for running Cypher queries and converting results.
    No state, no instances, no loading.
    """

    # ---------------------------------------------------------
    # Neo4j execution
    # ---------------------------------------------------------
    @staticmethod
    def run_cypher(query):
        driver = GraphDatabase.driver(
            f"bolt://{settings.NEO4J_HOST}:{settings.NEO4J_PORT}",
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
        )
        with driver.session() as session:
            records = list(session.run(query))
        driver.close()
        return records

    # ---------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------
    @staticmethod
    def resolve_label(node):
        labels = list(node.labels) if getattr(node, "labels", None) else []
        return labels[0] if labels else "Node"

    @staticmethod
    def get_props(n):
        props = dict(n._properties)
        data = json.loads(props.get("data", "{}"))
        props.pop("data", None)
        props.update(data)
        return props

    @staticmethod
    def records_to_networkx(records):
        G = nx.DiGraph()

        for record in records:
            n, m, r = record["n"], record["m"], record["r"]

            props_n = CypherQueryHelper.get_props(n)
            label_n = props_n.get("type", CypherQueryHelper.resolve_label(n))
            G.add_node(str(n.id), label=label_n, **props_n)

            props_m = CypherQueryHelper.get_props(m)
            label_m = props_m.get("type", CypherQueryHelper.resolve_label(m))
            G.add_node(str(m.id), label=label_m, **props_m)

            edge_type = r._properties.get("label", r.type)
            props_r = dict(r._properties)
            G.add_edge(str(n.id), str(m.id), type=edge_type, **props_r)

        return G

    # ---------------------------------------------------------
    # Lambda execution
    # ---------------------------------------------------------
    @staticmethod
    def apply_user_lambda(code, graph, name="cypher_lambda"):
        if not code:
            return graph

        executor = RestrictedPythonExecutor(
            code=code,
            context={"G": graph},
            name=name
        )

        result = executor.execute(extra_globals={"nx": nx})

        if isinstance(result, dict) and "error" in result:
            return graph

        return result if result is not None else graph

    # ---------------------------------------------------------
    # Convert NetworkX → Cytoscape JSON
    # ---------------------------------------------------------
    @staticmethod
    def networkx_to_elements(G, style):
        elements = []
        node_labels = set()
        edge_types = set()

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
    # Main entry point
    # ---------------------------------------------------------
    @staticmethod
    def run(query_obj):
        """
        query_obj must have:
            - query (Cypher string)
            - code (optional lambda)
            - node_styles / edge_styles (optional)
        """

        # 1. Run Cypher
        records = CypherQueryHelper.run_cypher(query_obj.query)

        # 2. Convert to NetworkX
        G = CypherQueryHelper.records_to_networkx(records)

        # 3. Apply lambda
        G = CypherQueryHelper.apply_user_lambda(
            code=query_obj.code,
            graph=G,
            name=f"cypher_query_{query_obj.id}"
        )

        # 4. Style resolver
        style = GraphStyleResolver(query_obj)

        # 5. Convert to Cytoscape JSON
        elements, node_labels, edge_types = CypherQueryHelper.networkx_to_elements(G, style)

        return {
            "elements": elements,
            "node_labels": list(node_labels),
            "edge_types": list(edge_types),
        }
