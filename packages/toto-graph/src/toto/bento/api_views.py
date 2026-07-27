"""JSON / CORS API for Bento, routed entirely through :mod:`graph_service`.

A small local CORS base keeps bento decoupled from studio-only apps (the old
code imported the chat app's ``CorsApiView``; bento is now a Neo4j-group app and
must not depend on the studio group).
"""

import json

from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from . import graph_service as gs
from .models import BentoCategory, BentoEdgeType


def _cors(response):
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
    response["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response


@method_decorator(csrf_exempt, name="dispatch")
class CorsApiView(View):
    def dispatch(self, request, *args, **kwargs):
        if request.method == "OPTIONS":
            return _cors(HttpResponse())
        try:
            response = super().dispatch(request, *args, **kwargs)
        except gs.GraphUnavailable as exc:
            response = JsonResponse({"error": str(exc)}, status=503)
        except gs.GraphValidationError as exc:
            response = JsonResponse({"error": str(exc)}, status=400)
        except gs.NotFound as exc:
            response = JsonResponse({"error": str(exc)}, status=404)
        return _cors(response)

    def _json(self, request):
        try:
            return json.loads(request.body or "{}")
        except (json.JSONDecodeError, ValueError):
            return None

    def _auth(self, request):
        return bool(request.user and request.user.is_authenticated)


def _page(request):
    try:
        per_page = max(1, min(int(request.GET.get("per_page", 25)), 200))
    except ValueError:
        per_page = 25
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        page = 1
    return page, per_page, (page - 1) * per_page


class NodeListApiView(CorsApiView):
    def get(self, request):
        page, per_page, offset = _page(request)
        rows, total = gs.list_nodes(
            cat_slug=request.GET.get("category") or None,
            q=request.GET.get("q"),
            limit=per_page,
            offset=offset,
        )
        return JsonResponse({"nodes": rows, "total": total, "page": page, "per_page": per_page})

    def post(self, request):
        if not self._auth(request):
            return JsonResponse({"error": "Not authenticated."}, status=401)
        data = self._json(request)
        if data is None:
            return JsonResponse({"error": "Invalid JSON."}, status=400)
        node = gs.create_node(data.get("category"), data.get("properties") or {})
        return JsonResponse(node, status=201)


class NodeDetailApiView(CorsApiView):
    def get(self, request, uid):
        return JsonResponse(gs.get_node(uid))

    def patch(self, request, uid):
        if not self._auth(request):
            return JsonResponse({"error": "Not authenticated."}, status=401)
        data = self._json(request)
        if data is None:
            return JsonResponse({"error": "Invalid JSON."}, status=400)
        return JsonResponse(gs.update_node(uid, data.get("properties") or {}))

    def delete(self, request, uid):
        if not self._auth(request):
            return JsonResponse({"error": "Not authenticated."}, status=401)
        gs.delete_node(uid)
        return JsonResponse({}, status=204)


class EdgeListApiView(CorsApiView):
    def get(self, request):
        page, per_page, offset = _page(request)
        rows, total = gs.list_edges(
            node_uid=request.GET.get("node") or None,
            et_slug=request.GET.get("edge_type") or None,
            q=request.GET.get("q"),
            limit=per_page,
            offset=offset,
        )
        return JsonResponse({"edges": rows, "total": total, "page": page, "per_page": per_page})

    def post(self, request):
        if not self._auth(request):
            return JsonResponse({"error": "Not authenticated."}, status=401)
        data = self._json(request)
        if data is None:
            return JsonResponse({"error": "Invalid JSON."}, status=400)
        edge = gs.create_edge(
            data.get("edge_type"), data.get("from"), data.get("to"), data.get("properties") or {}
        )
        return JsonResponse(edge, status=201)


class EdgeDetailApiView(CorsApiView):
    def patch(self, request, edge_id):
        if not self._auth(request):
            return JsonResponse({"error": "Not authenticated."}, status=401)
        data = self._json(request)
        if data is None:
            return JsonResponse({"error": "Invalid JSON."}, status=400)
        return JsonResponse(gs.update_edge(edge_id, data.get("properties") or {}))

    def delete(self, request, edge_id):
        if not self._auth(request):
            return JsonResponse({"error": "Not authenticated."}, status=401)
        gs.delete_edge(edge_id)
        return JsonResponse({}, status=204)


class CategoryListApiView(CorsApiView):
    def get(self, request):
        cats = [
            {"slug": c.slug, "name": c.name, "neo4j_label": c.neo4j_label,
             "property_schema": c.property_schema}
            for c in BentoCategory.objects.all()
        ]
        return JsonResponse({"categories": cats})


class EdgeTypeListApiView(CorsApiView):
    def get(self, request):
        ets = [
            {"slug": e.slug, "name": e.name, "rel_type": e.rel_type,
             "directed": e.directed,
             "allowed_sources": list(e.allowed_sources.values_list("slug", flat=True)),
             "allowed_targets": list(e.allowed_targets.values_list("slug", flat=True)),
             "property_schema": e.property_schema}
            for e in BentoEdgeType.objects.all()
        ]
        return JsonResponse({"edge_types": ets})


class FullGraphApiView(CorsApiView):
    def get(self, request):
        return JsonResponse(gs.full_graph(
            cat_slug=request.GET.get("category") or None,
            et_slug=request.GET.get("edge_type") or None,
            q=request.GET.get("q"),
        ))


class NodeGraphApiView(CorsApiView):
    def get(self, request, uid):
        try:
            depth = int(request.GET.get("depth", 1))
        except ValueError:
            depth = 1
        return JsonResponse(gs.node_graph(uid, depth=depth))
