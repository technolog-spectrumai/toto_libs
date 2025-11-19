from django.contrib.auth.decorators import login_required
from django.shortcuts import render
import json
import networkx as nx
from neo4j import GraphDatabase
from django.conf import settings
from oya.page import PageProcessor


def run_cypher(query: str):
    driver = GraphDatabase.driver(
        f"bolt://{settings.NEO4J_HOST}:{settings.NEO4J_PORT}",
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
    )
    with driver.session() as session:
        result = session.run(query)
        records = list(result)  # materialize all records before returning
    driver.close()
    return records


@login_required
def graph_explorer(request):
    cypher_query = "MATCH (n)-[r]->(m) RETURN n,r,m LIMIT 500"
    results = run_cypher(cypher_query)

    # Build a NetworkX graph
    G = nx.Graph()
    node_labels = {}

    for record in results:
        n = record["n"]
        m = record["m"]
        r = record["r"]

        # Add nodes with labels
        if n.id not in G:
            G.add_node(str(n.id))
            node_labels[str(n.id)] = list(n.labels)[0] if n.labels else "Node"

        if m.id not in G:
            G.add_node(str(m.id))
            node_labels[str(m.id)] = list(m.labels)[0] if m.labels else "Node"

        # Add edge
        G.add_edge(str(n.id), str(m.id), type=r.type)

    # Compute spring layout positions
    pos = nx.spring_layout(G, k=0.5, iterations=50)

    # Convert to Cytoscape JSON format
    elements = []
    for node_id, coords in pos.items():
        elements.append({
            "data": {"id": node_id, "label": node_labels[node_id]},
            "position": {"x": float(coords[0]) * 500, "y": float(coords[1]) * 500}
        })

    for u, v, data in G.edges(data=True):
        elements.append({
            "data": {
                "id": f"{u}-{v}",
                "source": u,
                "target": v,
                "type": data.get("type", "")
            }
        })

    processor = PageProcessor()
    context = {
        "title": "Graph Explorer",
        "cypher_results_json": json.dumps(elements)
    }
    context = processor.decorate(context, request)
    return render(request, "ravioli/graph.html", context)
