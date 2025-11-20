from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.http import JsonResponse
from neo4j import GraphDatabase
from django.conf import settings
from oya.page import PageProcessor
from .models import CypherQuery


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


@login_required
def graph_explorer(request):
    """
    Render the Graph Explorer page shell.
    Data is fetched asynchronously via graph_data endpoint.
    """
    available_queries = CypherQuery.objects.filter(is_active=True).order_by("name")

    # Default to first query if none selected
    query_id = request.GET.get("query_id")
    if not query_id and available_queries.exists():
        query_id = str(available_queries.first().id)

    processor = PageProcessor()
    context = {
        "title": "Graph Explorer",
        "queries": available_queries,
        "selected_query": query_id,
    }
    context = processor.decorate(context, request)
    return render(request, "ravioli/graph.html", context)


@login_required
def graph_data(request):
    """
    Return Cypher query results as JSON for async fetch.
    """
    query_id = request.GET.get("query_id")
    if not query_id:
        first = CypherQuery.objects.filter(is_active=True).order_by("name").first()
        if first:
            query_id = str(first.id)

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
    node_labels = set()
    edge_types = set()

    for record in results:
        n, m, r = record["n"], record["m"], record["r"]

        if str(n.id) not in seen_nodes:
            seen_nodes.add(str(n.id))
            node_labels.update(n.labels)
            elements.append({
                "data": {
                    "id": str(n.id),
                    "label": list(n.labels)[0] if n.labels else "Node",
                    **n._properties
                }
            })

        if str(m.id) not in seen_nodes:
            seen_nodes.add(str(m.id))
            node_labels.update(m.labels)
            elements.append({
                "data": {
                    "id": str(m.id),
                    "label": list(m.labels)[0] if m.labels else "Node",
                    **m._properties
                }
            })
        edge_types.add(r.type)
        elements.append({
            "data": {
                "id": f"{n.id}-{m.id}",
                "source": str(n.id),
                "target": str(m.id),
                "type": r.type,
                **r._properties
            }
        })

    return JsonResponse({"elements": elements, "node_labels": list(node_labels), "edge_types": list(edge_types)})
