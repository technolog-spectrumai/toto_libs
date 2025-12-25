import json
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.http import JsonResponse
from oya.page import PageProcessor
from .models import CypherQuery
from .query import QueryHelper



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
    query_id = request.GET.get("query_id")

    # Default to first active query
    if not query_id:
        first = CypherQuery.objects.filter(is_active=True).order_by("name").first()
        if first:
            query_id = str(first.id)

    helper = QueryHelper(query_id)
    data = helper.run()

    return JsonResponse(data)

