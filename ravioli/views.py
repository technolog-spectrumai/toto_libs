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
@login_required
def graph_explorer(request):
    cypher_query = "MATCH (n)-[r]->(m) RETURN n,r,m LIMIT 500"
    results = run_cypher(cypher_query)

    elements = []
    seen_nodes = set()

    for record in results:
        n = record["n"]
        m = record["m"]
        r = record["r"]

        # Add node n if not already added
        if str(n.id) not in seen_nodes:
            seen_nodes.add(str(n.id))
            elements.append({
                "data": {
                    "id": str(n.id),
                    "label": list(n.labels)[0] if n.labels else "Node",
                    # include extra neomodel properties
                    **n._properties  # neomodel nodes expose properties dict
                }
            })

        # Add node m if not already added
        if str(m.id) not in seen_nodes:
            seen_nodes.add(str(m.id))
            elements.append({
                "data": {
                    "id": str(m.id),
                    "label": list(m.labels)[0] if m.labels else "Node",
                    **m._properties
                }
            })

        # Add edge
        elements.append({
            "data": {
                "id": f"{n.id}-{m.id}",
                "source": str(n.id),
                "target": str(m.id),
                "type": r.type,
                **r._properties  # relationship properties
            }
        })

    processor = PageProcessor()
    context = {
        "title": "Graph Explorer",
        "cypher_results_json": json.dumps(elements)
    }
    context = processor.decorate(context, request)
    return render(request, "ravioli/graph.html", context)

