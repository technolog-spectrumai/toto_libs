import json

from celery.bin.result import result
from django.contrib.auth.decorators import login_required
from oya.page import PageProcessor
from .models import CypherQuery, DynamicPage
from .graph_lambda import GraphLambdaHelper
from django.http import JsonResponse, Http404, HttpResponseServerError
from django.shortcuts import render, get_object_or_404
from django.views import View
from .style import GraphStyleResolver
from ravioli.builder import GraphBuilder
from .query import CypherQueryHelper


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

import json
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from vault.client import VaultClient
from vault.models import VaultFile
from .models import Gateway


@login_required
@require_POST
def gateway_upload(request, slug):
    """
    Handle file drop into a Gateway.
    1. Resolve gateway
    2. Upload file into its workflow bucket
    3. Execute workflow
    4. Return workflow result as JSON
    """

    gateway = get_object_or_404(Gateway, slug=slug)
    workflow = gateway.workflow

    if not workflow.is_active:
        raise Http404("Workflow is not active")

    # Ensure file was provided
    uploaded_file = request.FILES.get("file")
    if not uploaded_file:
        return JsonResponse({"error": "No file uploaded"}, status=400)

    # Upload file into workflow bucket using VaultClient
    client = VaultClient(bucket_name=workflow.bucket.name)
    file_info = client.upload_file(uploaded_file, name=uploaded_file.name)

    # Retrieve the actual VaultFile instance
    try:
        vault_file = VaultFile.objects.get(
            key=file_info["key"],
            owner=request.user,
            bucket=workflow.bucket
        )
    except VaultFile.DoesNotExist:
        return JsonResponse({"error": "Uploaded file not found"}, status=500)

    # Execute workflow
    try:
        result = workflow.execute({
            "file": vault_file,
            "file_path": vault_file.file.path,
            "file_type": vault_file.file_type,
            "owner": vault_file.owner,
            "bucket": vault_file.bucket,
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

    # Must be JSON-serializable
    return JsonResponse({"result": result})

from django.views import View
from django.shortcuts import render

class GatewayView(View):
    template_name = "webfront/gateway.html"

    def get(self, request, slug):
        gateway = get_object_or_404(Gateway, slug=slug)
        processor = PageProcessor()
        context = processor.decorate({"gateway": gateway}, request)
        return render(request, self.template_name, context)




