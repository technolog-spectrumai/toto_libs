from django.utils.text import slugify
from oya.page import PageProcessor
from .files import FileHelper
from .models import CypherQuery, DynamicPage, Gateway
from .graph_lambda import GraphLambdaHelper
from .style import GraphStyleResolver
from ravioli.builder import GraphBuilder
from .query import CypherQueryHelper
from django.views import View
from django.shortcuts import render
import json
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST
from vault.client import VaultClient
from vault.models import VaultFile
import random
import string


def short_hash(length=3):
    return ''.join(random.choices(string.ascii_lowercase, k=length))


def save_file(uploaded_file, client):
    base_name = uploaded_file.name
    base_key = slugify(base_name)

    # Add 3‑letter hash
    key = f"{base_key}-{short_hash()}"

    # Ensure uniqueness inside bucket
    while client.file_exists(key):
        key = f"{base_key}-{short_hash()}"

    client.upload_file(uploaded_file, name=key)


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
        graph = GraphBuilder.build_graph(query_obj.query)
        graph = GraphLambdaHelper.apply_graph_lambda(
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
        result = []
        lambda_result = {}
        try:
            graph = GraphBuilder.build_graph(page.query)
            lambda_result = GraphLambdaHelper.apply_user_lambda(
                code=page.code,
                graph=graph,
                name=f"page_{page.id}"
            )
        except GraphLambdaHelper.Error as e:
            result.append({
                "id": f"error_{page.id}",
                "title": "Error",
                "error": str(e),
                "data": None
            })
        if not isinstance(result, list):
            result.append({
                "id": f"error_{page.id}",
                "title": "Error",
                "error": "Page code must return a list of widgets",
                "data": None
            })
        else:
            result.extend([
                {"data": json.dumps(i), "id": i["id"], "title": i["title"]}
                if "error" not in i else i
                for i in lambda_result
            ])
        context = processor.decorate({"page": page, "result": result}, request)
        return render(request, self.template_name, context)


@login_required
@require_POST
def gateway_upload(request, slug):
    """
    Handle file drop into a Gateway.
    1. Resolve gateway
    2. Save file into workflow bucket using FileHelper
    3. Execute workflow (disabled here)
    4. Return result as JSON
    """

    gateway = get_object_or_404(Gateway, slug=slug)
    workflow = gateway.workflow

    if not workflow.is_active:
        raise Http404("Workflow is not active")

    uploaded_file = request.FILES.get("file")
    if not uploaded_file:
        return JsonResponse({"error": "No file uploaded"}, status=400)

    # Use FileHelper instead of VaultClient
    helper = FileHelper(bucket_name=workflow.bucket.name)

    # Save file (returns VaultFile instance)
    vault_file = helper.save_file(uploaded_file)

    if not vault_file:
        return JsonResponse({"error": "File could not be saved"}, status=500)

    # Example result (workflow disabled)
    result = {
        "file_name": vault_file.title,
        "file_key": vault_file.key,
        "file_size_bytes": vault_file.file.size,
    }

    return JsonResponse({"result": result})




class GatewayView(View):
    template_name = "webfront/gateway.html"

    def get(self, request, slug):
        gateway = get_object_or_404(Gateway, slug=slug)
        processor = PageProcessor()
        context = processor.decorate({"gateway": gateway}, request)
        return render(request, self.template_name, context)




