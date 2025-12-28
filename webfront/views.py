import json
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from oya.page import PageProcessor
from .models import CypherQuery, DynamicPage
from .query import CypherQueryHelper
from django.http import JsonResponse, Http404
from django.shortcuts import render, get_object_or_404
from django.views import View
from .style import GraphStyleResolver


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
    return render(request, "webfront/graph.html", context)



@login_required
def graph_data(request):
    query_id = request.GET.get("query_id")

    # Default to first active query
    if not query_id:
        first = CypherQuery.objects.filter(is_active=True).order_by("name").first()
        if not first:
            raise Http404("No active Cypher queries found")
        query_id = first.id

    # Load query object
    try:
        query_obj = CypherQuery.objects.get(pk=query_id, is_active=True)
    except CypherQuery.DoesNotExist:
        raise Http404("Query not found")

    try:
        graph = CypherQueryHelper.build_graph(query_obj.query)
        graph = CypherQueryHelper.apply_graph_lambda(
            code=query_obj.code,
            graph=graph,
            name=f"cypher_query_{query_obj.id}"
        )
        style = GraphStyleResolver(query_obj)
        data = CypherQueryHelper.graph_to_cytoscape(graph, style)
    except Exception as e:
        raise Http404(f"Error executing query: {str(e)}")

    return JsonResponse(data)


class DynamicPageView(View):

    template_name = "webfront/page.html"

    def get(self, request, slug):
        page = get_object_or_404(DynamicPage, slug=slug)
        processor = PageProcessor()

        try:
            graph = CypherQueryHelper.build_graph(page.query)
            result = CypherQueryHelper.apply_user_lambda(
                code=page.code,
                graph=graph,
                name=f"page_{page.id}"
            )
        except Exception as e:
            raise Http404(f"Error executing query: {str(e)}")
        if not isinstance(result, list):
            raise Http404("Page code must return a list of widgets")
        result = [{"data": json.dumps(i), "id": i["id"], "title":i["title"]} for i in result]
        context = processor.decorate({"page": page, "result": result}, request)
        return render(request, self.template_name, context)





