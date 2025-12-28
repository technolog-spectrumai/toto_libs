import networkx as nx
from toto.executor import RestrictedPythonExecutor
from .style import GraphStyleResolver
import numpy as np
from ravioli.builder import GraphBuilder


class CypherQueryHelper(GraphBuilder):
    """
    Pure static helper for running Cypher queries and converting results.
    No state, no instances, no loading.
    """

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
            result = executor.execute(extra_globals={ "nx": nx, "np": np } )
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


    @staticmethod
    def graph_to_cytoscape(graph, style):
        elements, node_labels, edge_types = CypherQueryHelper.networkx_to_elements(graph, style)
        return {
            "elements": elements,
            "node_labels": list(node_labels),
            "edge_types": list(edge_types),
        }


