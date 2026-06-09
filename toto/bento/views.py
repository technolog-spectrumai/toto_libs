"""Server-rendered Bento UI: a Neo4j graph editor.

All graph access goes through :mod:`graph_service`; nothing here imports neo4j or
neomodel. Node/edge lists are paginated, filterable by type, quick-searchable,
and support batch delete. The graph view lazily expands neighborhoods.
"""

import json
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from toto.ui import PageProcessor

from . import graph_service as gs
from .forms import (
    BentoCategoryForm,
    BentoEdgeTypeForm,
    build_node_form,
    collect_props,
)
from .models import BentoCategory, BentoEdgeType


def bento_render(request, template_name, context):
    return render(request, template_name, PageProcessor().decorate(context, request))


def _unavailable(request, exc):
    return bento_render(request, "bento/unavailable.html", {"error": str(exc)})


def graph_view(fn):
    """Login-required + graceful handling of a disabled/missing graph."""

    @wraps(fn)
    @login_required
    def wrapper(request, *args, **kwargs):
        try:
            return fn(request, *args, **kwargs)
        except gs.GraphUnavailable as exc:
            return _unavailable(request, exc)
        except gs.NotFound:
            raise Http404()

    return wrapper


def _page_params(request, default=25):
    try:
        per_page = max(1, min(int(request.GET.get("per_page", default)), 200))
    except (TypeError, ValueError):
        per_page = default
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    return page, per_page, (page - 1) * per_page


def _page_context(page, per_page, total):
    num_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, num_pages)
    return {
        "page": page, "per_page": per_page, "total": total, "num_pages": num_pages,
        "has_prev": page > 1, "has_next": page < num_pages,
        "prev": page - 1, "next": page + 1,
        "start_index": 0 if total == 0 else (page - 1) * per_page + 1,
        "end_index": min(page * per_page, total),
        "page_range": range(1, num_pages + 1),
    }


# --------------------------------------------------------------------------
# nodes
# --------------------------------------------------------------------------

@graph_view
def node_list(request):
    page, per_page, offset = _page_params(request)
    category = request.GET.get("category") or ""
    q = request.GET.get("q", "")
    rows, total = gs.list_nodes(cat_slug=category or None, q=q, limit=per_page, offset=offset)
    return bento_render(request, "bento/node_list.html", {
        "nodes": rows,
        "categories": BentoCategory.objects.all(),
        "active_category": category,
        "query": q,
        "pagination": _page_context(page, per_page, total),
    })


@graph_view
def node_detail(request, uid):
    node = gs.get_node(uid)
    edges, _total = gs.list_edges(node_uid=uid, limit=200, offset=0)
    return bento_render(request, "bento/node_detail.html", {
        "node": node,
        "edges": edges,
        "edge_types": BentoEdgeType.objects.all(),
    })


@graph_view
def node_create(request):
    slug = request.GET.get("category") or request.POST.get("category")
    category = BentoCategory.objects.filter(slug=slug).first() if slug else None
    if not category:
        return bento_render(request, "bento/node_pick_category.html", {
            "categories": BentoCategory.objects.all(),
        })

    if request.method == "POST":
        form = build_node_form(category, data=request.POST)
        if form.is_valid():
            try:
                node = gs.create_node(category.slug, collect_props(form))
                messages.success(request, _("Node created."))
                return redirect("bento:node_detail", uid=node["uid"])
            except (gs.GraphValidationError, ValueError) as exc:
                form.add_error(None, str(exc))
    else:
        form = build_node_form(category)
    return bento_render(request, "bento/node_form.html", {
        "form": form, "category": category, "title": _("New %(name)s") % {"name": category.name},
    })


@graph_view
def node_update(request, uid):
    node = gs.get_node(uid)
    category = get_object_or_404(BentoCategory, slug=node["category_slug"])
    props = node["properties"]
    initial = {k: props.get(k) for k in props}
    if isinstance(props.get("extra"), dict):
        import json
        initial["extra"] = json.dumps(props["extra"], indent=2) if props["extra"] else ""

    if request.method == "POST":
        form = build_node_form(category, data=request.POST)
        if form.is_valid():
            try:
                gs.update_node(uid, collect_props(form))
                messages.success(request, _("Node updated."))
                return redirect("bento:node_detail", uid=uid)
            except (gs.GraphValidationError, ValueError) as exc:
                form.add_error(None, str(exc))
    else:
        form = build_node_form(category, initial=initial)
    return bento_render(request, "bento/node_form.html", {
        "form": form, "category": category, "node": node, "title": _("Edit node"),
    })


@graph_view
def node_delete(request, uid):
    node = gs.get_node(uid)
    if request.method == "POST":
        gs.delete_node(uid)
        messages.success(request, _("Node deleted."))
        return redirect("bento:node_list")
    return bento_render(request, "bento/node_confirm_delete.html", {"node": node})


@graph_view
def node_batch_delete(request):
    if request.method == "POST":
        uids = request.POST.getlist("uids")
        count = gs.delete_nodes(uids)
        messages.success(request, _("%(n)d node(s) deleted.") % {"n": count})
    return redirect(request.POST.get("next") or "bento:node_list")


# --------------------------------------------------------------------------
# edges
# --------------------------------------------------------------------------

@graph_view
def edge_list(request):
    page, per_page, offset = _page_params(request)
    et = request.GET.get("edge_type") or ""
    q = request.GET.get("q", "")
    rows, total = gs.list_edges(et_slug=et or None, q=q, limit=per_page, offset=offset)
    return bento_render(request, "bento/edge_list.html", {
        "edges": rows,
        "edge_types": BentoEdgeType.objects.all(),
        "active_edge_type": et,
        "query": q,
        "pagination": _page_context(page, per_page, total),
    })


def _parse_json_object(raw):
    """Parse a JSON-object textarea. Returns (dict, error_message_or_None)."""
    raw = (raw or "").strip()
    if not raw:
        return {}, None
    try:
        obj = json.loads(raw)
    except ValueError as exc:
        return {}, _("Data must be valid JSON: %(e)s") % {"e": exc}
    if not isinstance(obj, dict):
        return {}, _("Data must be a JSON object.")
    return obj, None


@graph_view
def edge_create(request):
    """One simple form: from-node, edge type, to-node, and a JSON data box."""
    from_uid = (request.POST.get("from_uid") or request.GET.get("from") or "").strip()
    to_uid = (request.POST.get("to_uid") or request.GET.get("to") or "").strip()
    et_slug = (request.POST.get("edge_type") or request.GET.get("edge_type") or "").strip()
    data_raw = request.POST.get("data", "")
    error = None

    if request.method == "POST":
        props, error = _parse_json_object(data_raw)
        if error is None and not (from_uid and to_uid and et_slug):
            error = _("Choose an edge type, a from-node and a to-node.")
        if error is None and from_uid == to_uid:
            error = _("A node cannot link to itself.")
        if error is None:
            try:
                gs.create_edge(et_slug, from_uid, to_uid, props)
                messages.success(request, _("Edge created."))
                return redirect("bento:node_detail", uid=from_uid)
            except (gs.GraphValidationError, gs.NotFound, ValueError) as exc:
                error = str(exc)

    nodes, _total = gs.list_nodes(limit=500)
    return bento_render(request, "bento/edge_form.html", {
        "edge_types": BentoEdgeType.objects.all(),
        "nodes": nodes,
        "selected": {"from_uid": from_uid, "to_uid": to_uid, "edge_type": et_slug, "data": data_raw},
        "error": error,
        "title": _("New edge"),
    })


@graph_view
def edge_update(request, edge_id):
    edge = gs.get_edge(edge_id)
    error = None

    if request.method == "POST":
        data_raw = request.POST.get("data", "")
        props, error = _parse_json_object(data_raw)
        if error is None:
            try:
                gs.update_edge(edge_id, props)
                messages.success(request, _("Edge updated."))
                return redirect("bento:node_detail", uid=edge["source"])
            except (gs.GraphValidationError, ValueError) as exc:
                error = str(exc)
    else:
        # seed the data box with the flat properties (extra merged in)
        flat = {k: v for k, v in edge["properties"].items() if k != "extra"}
        flat.update(edge["properties"].get("extra") or {})
        data_raw = json.dumps(flat, indent=2) if flat else ""

    return bento_render(request, "bento/edge_form.html", {
        "edge": edge,
        "selected": {"data": data_raw},
        "error": error,
        "title": _("Edit edge"),
    })


@graph_view
def edge_delete(request, edge_id):
    edge = gs.get_edge(edge_id)
    if request.method == "POST":
        gs.delete_edge(edge_id)
        messages.success(request, _("Edge deleted."))
        return redirect(request.POST.get("next") or "bento:edge_list")
    return bento_render(request, "bento/edge_confirm_delete.html", {"edge": edge})


@graph_view
def edge_batch_delete(request):
    if request.method == "POST":
        ids = request.POST.getlist("ids")
        count = gs.delete_edges(ids)
        messages.success(request, _("%(n)d edge(s) deleted.") % {"n": count})
    return redirect(request.POST.get("next") or "bento:edge_list")


# --------------------------------------------------------------------------
# graph (lazy) JSON endpoints
# --------------------------------------------------------------------------

@login_required
def api_full_graph(request):
    try:
        return JsonResponse(gs.full_graph(
            cat_slug=request.GET.get("category") or None,
            et_slug=request.GET.get("edge_type") or None,
            q=request.GET.get("q"),
        ))
    except gs.GraphUnavailable as exc:
        return JsonResponse({"error": str(exc), "nodes": [], "edges": []}, status=503)


@login_required
def api_node_graph(request, uid):
    try:
        depth = int(request.GET.get("depth", 1))
    except (TypeError, ValueError):
        depth = 1
    try:
        return JsonResponse(gs.node_graph(uid, depth=depth))
    except gs.GraphUnavailable as exc:
        return JsonResponse({"error": str(exc), "nodes": [], "edges": []}, status=503)


# --------------------------------------------------------------------------
# templates: categories
# --------------------------------------------------------------------------

@login_required
def category_list(request):
    return bento_render(request, "bento/category_list.html", {
        "categories": BentoCategory.objects.all(),
    })


@login_required
def category_create(request):
    if request.method == "POST":
        form = BentoCategoryForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("bento:category_list")
    else:
        form = BentoCategoryForm()
    return bento_render(request, "bento/category_form.html", {"form": form, "title": _("New category template")})


@login_required
def category_update(request, pk):
    category = get_object_or_404(BentoCategory, pk=pk)
    if request.method == "POST":
        form = BentoCategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            return redirect("bento:category_list")
    else:
        form = BentoCategoryForm(instance=category)
    return bento_render(request, "bento/category_form.html", {
        "form": form, "category": category, "title": _("Edit category template"),
    })


@login_required
def category_delete(request, pk):
    category = get_object_or_404(BentoCategory, pk=pk)
    if request.method == "POST":
        category.delete()
        return redirect("bento:category_list")
    return bento_render(request, "bento/category_confirm_delete.html", {"category": category})


# --------------------------------------------------------------------------
# templates: edge types
# --------------------------------------------------------------------------

@login_required
def edgetype_list(request):
    return bento_render(request, "bento/edgetype_list.html", {
        "edge_types": BentoEdgeType.objects.all(),
    })


@login_required
def edgetype_create(request):
    if request.method == "POST":
        form = BentoEdgeTypeForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("bento:edgetype_list")
    else:
        form = BentoEdgeTypeForm()
    return bento_render(request, "bento/edgetype_form.html", {"form": form, "title": _("New edge-type template")})


@login_required
def edgetype_update(request, pk):
    edge_type = get_object_or_404(BentoEdgeType, pk=pk)
    if request.method == "POST":
        form = BentoEdgeTypeForm(request.POST, instance=edge_type)
        if form.is_valid():
            form.save()
            return redirect("bento:edgetype_list")
    else:
        form = BentoEdgeTypeForm(instance=edge_type)
    return bento_render(request, "bento/edgetype_form.html", {
        "form": form, "edge_type": edge_type, "title": _("Edit edge-type template"),
    })


@login_required
def edgetype_delete(request, pk):
    edge_type = get_object_or_404(BentoEdgeType, pk=pk)
    if request.method == "POST":
        edge_type.delete()
        return redirect("bento:edgetype_list")
    return bento_render(request, "bento/edgetype_confirm_delete.html", {"edge_type": edge_type})
