from toto.executor import RestrictedPythonExecutor


class CypherQueryHelper:

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


