from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, Http404
from django.shortcuts import render, get_object_or_404
from oya.page import PageProcessor
from ravioli.builder import GraphBuilder
from webfront.models import CypherQuery
from webfront.style import GraphStyleResolver
from webfront.query import CypherQueryHelper
import random
import string


def short_hash(length=3):
    return ''.join(random.choices(string.ascii_lowercase, k=length))


@login_required
def graph_explorer(request):
    """
    Render the Graph Explorer page shell.
    Data is fetched asynchronously via graph_data endpoint.
    """
    available_queries = CypherQuery.objects.order_by("name")

    query_id = request.GET.get("query_id")
    if not query_id and available_queries.exists():
        query_id = str(available_queries.first().id)

    processor = PageProcessor()
    context = processor.decorate({
        "title": "Graph Explorer",
        "queries": available_queries,
        "selected_query": query_id,
    }, request)

    return render(request, "webfront/graph.html", context)



@login_required
def graph_data(request):
    query_id = request.GET.get("query_id")

    if not query_id:
        first = CypherQuery.objects.filter(is_active=True).order_by("name").first()
        if not first:
            raise Http404("No active Cypher queries found")
        query_id = first.id

    query_obj = get_object_or_404(CypherQuery, pk=query_id, is_active=True)

    try:
        # Build graph from Cypher
        graph = GraphBuilder.build_graph(query_obj.query)

        # Apply lambda if present
        if query_obj.lambda_node:
            graph = query_obj.lambda_node.execute({"G": graph}) or graph

        # Style + convert to Cytoscape JSON
        style = GraphStyleResolver(query_obj)
        data = CypherQueryHelper.graph_to_cytoscape(graph, style)

    except Exception as e:
        raise Http404(f"Error executing query: {str(e)}")

    return JsonResponse(data)
