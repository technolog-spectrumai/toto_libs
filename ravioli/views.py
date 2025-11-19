from django.contrib.auth.decorators import login_required
from django.shortcuts import render
import json
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

    elements = []
    seen_nodes = set()

    for record in results:
        n = record["n"]
        m = record["m"]
        r = record["r"]

        if n.id not in seen_nodes:
            elements.append({"data": {"id": str(n.id), "label": list(n.labels)[0]}})
            seen_nodes.add(n.id)

        if m.id not in seen_nodes:
            elements.append({"data": {"id": str(m.id), "label": list(m.labels)[0]}})
            seen_nodes.add(m.id)

        elements.append({
            "data": {
                "id": str(r.id),
                "source": str(n.id),
                "target": str(m.id),
                "type": r.type
            }
        })
    processor = PageProcessor()
    context = {
        "title": "Graph Explorer",
        "cypher_results_json": json.dumps(elements)
    }
    context = processor.decorate(context, request)
    return render(request, "ravioli/graph.html", context)
