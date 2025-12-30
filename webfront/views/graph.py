from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, Http404
from django.shortcuts import render, get_object_or_404
from oya.page import PageProcessor
from ravioli.builder import GraphBuilder
from webfront.models import CypherQuery, GraphWorkflow
from webfront.style import GraphStyleResolver
from webfront.query import CypherQueryHelper
import random
import string



def short_hash(length=3):
    return ''.join(random.choices(string.ascii_lowercase, k=length))


@login_required
def graph_explorer(request):
    workflows = GraphWorkflow.objects.filter(is_active=True).order_by("name")

    workflow_id = request.GET.get("workflow_id")
    if not workflow_id and workflows.exists():
        workflow_id = str(workflows.first().id)
    queries = [i.cypher_query for i in workflows]
    processor = PageProcessor()
    context = processor.decorate({
        "title": "Graph Explorer",
        "queries": workflows,
        "selected_query": workflow_id,
    }, request)

    return render(request, "webfront/graph.html", context)


@login_required
def graph_data(request):
    workflow_id = request.GET.get("query_id") or request.GET.get("workflow_id")

    if not workflow_id:
        first = GraphWorkflow.objects.filter(is_active=True).order_by("name").first()
        if not first:
            raise Http404("No active graph workflows found")
        workflow_id = first.id

    workflow = get_object_or_404(GraphWorkflow, pk=workflow_id, is_active=True)
    query_obj = workflow.cypher_query

    try:
        graph = GraphBuilder.build_graph(query_obj.query)

        if workflow.lambda_node:
            graph = workflow.lambda_node.execute({"G": graph}) or graph

        style = GraphStyleResolver(query_obj)
        data = CypherQueryHelper.graph_to_cytoscape(graph, style)

    except Exception as e:
        raise Http404(f"Error executing workflow: {str(e)}")

    return JsonResponse(data)

