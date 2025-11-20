from django.contrib.auth.decorators import login_required
from django.shortcuts import render
import json
from neo4j import GraphDatabase
from django.conf import settings
from oya.page import PageProcessor
from .models import CypherQuery   # import your model


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
    # If a query id is passed in GET, use that; otherwise default
    query_id = request.GET.get("query_id")
    if query_id:
        try:
            cypher_query = CypherQuery.objects.get(pk=query_id, is_active=True).query
        except CypherQuery.DoesNotExist:
            cypher_query = "MATCH (n)-[r]->(m) RETURN n,r,m LIMIT 500"
    else:
        cypher_query = "MATCH (n)-[r]->(m) RETURN n,r,m LIMIT 500"

    results = run_cypher(cypher_query)

    elements = []
    seen_nodes = set()

    for record in results:
        n = record["n"]
        m = record["m"]
        r = record["r"]

        if str(n.id) not in seen_nodes:
            seen_nodes.add(str(n.id))
            elements.append({
                "data": {
                    "id": str(n.id),
                    "label": list(n.labels)[0] if n.labels else "Node",
                    **n._properties
                }
            })

        if str(m.id) not in seen_nodes:
            seen_nodes.add(str(m.id))
            elements.append({
                "data": {
                    "id": str(m.id),
                    "label": list(m.labels)[0] if m.labels else "Node",
                    **m._properties
                }
            })

        elements.append({
            "data": {
                "id": f"{n.id}-{m.id}",
                "source": str(n.id),
                "target": str(m.id),
                "type": r.type,
                **r._properties
            }
        })

    # Fetch all active predefined queries to send to template
    available_queries = CypherQuery.objects.filter(is_active=True).order_by("name")

    processor = PageProcessor()
    context = {
        "title": "Graph Explorer",
        "cypher_results_json": json.dumps(elements),
        "queries": available_queries,   # pass list of queries
        "selected_query": query_id,
    }
    context = processor.decorate(context, request)
    return render(request, "ravioli/graph.html", context)
