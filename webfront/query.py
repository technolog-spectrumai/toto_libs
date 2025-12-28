import json
import networkx as nx
from neo4j import GraphDatabase
from django.conf import settings
from toto.executor import RestrictedPythonExecutor
from .style import GraphStyleResolver
import numpy as np


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

    @staticmethod
    def apply_user_lambda(code, graph, name="cypher_lambda"):
        if not code:
            return graph

        # Prepare context
        context = {"G": graph}

        executor = RestrictedPythonExecutor(
            code=code,
            context=context,
            name=name
        )
        result = {}
        try:
            result = executor.execute(extra_globals={"nx": nx, "np": np})
        except Exception as e:
            result["error"] = str(e)

        return result


    @staticmethod
    def apply_graph_lambda(code, graph, name="cypher_lambda"):
        result = CypherQueryHelper.apply_user_lambda(code, graph, name)

        # If executor returned an error dict → bail out
        if isinstance(result, dict) and "error" in result:
            return graph

        # Enforce graph-only return
        if not isinstance(result, nx.Graph):
            return graph

        return result

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
            node_data = {
                "id": node_id,
                "label": label,
                "color": style.node_color(label) if style else None,
                "size": style.node_size(label) if style else None,
                **data
            }
            elements.append({
                "data": node_data
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

    # @staticmethod
    # def networkx_to_simple_graph(G):
    #     nodes = []
    #     edges = []
    #
    #     # Nodes
    #     for node_id, data in G.nodes(data=True):
    #         node = {
    #             "id": node_id,
    #             "label": data.get("label", "Node"),
    #         }
    #
    #         # Add all other node attributes
    #         for k, v in data.items():
    #             if k not in ("label",):
    #                 node[k] = v
    #
    #         nodes.append(node)
    #
    #     # Edges
    #     for u, v, data in G.edges(data=True):
    #         edge = {
    #             "source": u,
    #             "target": v,
    #             "type": data.get("type", "REL"),
    #         }
    #
    #         # Add all other edge attributes
    #         for k, v in data.items():
    #             if k not in ("type",):
    #                 edge[k] = v
    #
    #         edges.append(edge)
    #
    #     return {
    #         "nodes": nodes,
    #         "edges": edges,
    #     }

    @staticmethod
    def build_graph(query):
        records = CypherQueryHelper.run_cypher(query)
        return CypherQueryHelper.records_to_networkx(records)

    @staticmethod
    def graph_to_cytoscape(graph, style):
        elements, node_labels, edge_types = CypherQueryHelper.networkx_to_elements(graph, style)
        return {
            "elements": elements,
            "node_labels": list(node_labels),
            "edge_types": list(edge_types),
        }

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
        G = CypherQueryHelper.apply_graph_lambda(
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


