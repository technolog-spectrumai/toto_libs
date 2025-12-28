import json
import networkx as nx
from neo4j import GraphDatabase
from django.conf import settings
from toto.executor import RestrictedPythonExecutor


class GraphBuilder:

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

            props_n = GraphBuilder.get_props(n)
            label_n = props_n.get("type", GraphBuilder.resolve_label(n))
            G.add_node(str(n.id), label=label_n, **props_n)

            props_m = GraphBuilder.get_props(m)
            label_m = props_m.get("type", GraphBuilder.resolve_label(m))
            G.add_node(str(m.id), label=label_m, **props_m)

            edge_type = r._properties.get("label", r.type)
            props_r = dict(r._properties)
            G.add_edge(str(n.id), str(m.id), type=edge_type, **props_r)

        return G


    @staticmethod
    def build_graph(query):
        records = GraphBuilder.run_cypher(query)
        return GraphBuilder.records_to_networkx(records)


