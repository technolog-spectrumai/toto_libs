"""Server-rendered Bento UI: a Neo4j graph editor.

All graph access goes through :mod:`graph_service`; nothing here imports neo4j or
neomodel. Node/edge lists are paginated, filterable by type, quick-searchable,
and support batch delete. The graph view lazily expands neighborhoods.
"""

import json
from functools import wraps
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from toto.ui import PageProcessor

from . import graph_service as gs
from .forms import BentoCategoryForm, BentoEdgeTypeForm
from .models import BentoCategory, BentoEdgeType


def bento_render(request, template_name, context):
    return render(request, template_name, PageProcessor().decorate(context, request))


def _unavailable(request, exc):
    return bento_render(request, "bento/unavailable.html", {"error": str(exc)})


def graph_view(fn):
    """Login-required + graceful handling of a disabled/missing/unreachable graph."""

    @wraps(fn)
    @login_required
    def wrapper(request, *args, **kwargs):
        try:
            return fn(request, *args, **kwargs)
        except gs.GraphUnavailable as exc:
            return _unavailable(request, exc)
        except gs.NotFound:
            raise Http404()
        except Exception as exc:
            # Neo4j is enabled but unreachable (driver down / connection refused):
            # render the graceful "unavailable" state instead of a raw 500.
            from toto.ravioli.connection import is_connection_error
            if is_connection_error(exc):
                return _unavailable(request, exc)
            raise

    return wrapper


# --------------------------------------------------------------------------
# nodes
# --------------------------------------------------------------------------

PER_PAGE_CHOICES = [10, 25, 50, 100]
DEFAULT_PER_PAGE = 25


def _paginate(request):
    try:
        per_page = int(request.GET.get("per_page", DEFAULT_PER_PAGE))
    except (TypeError, ValueError):
        per_page = DEFAULT_PER_PAGE
    if per_page not in PER_PAGE_CHOICES:
        per_page = DEFAULT_PER_PAGE
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    return page, per_page


def _page_context(page, per_page, total):
    num_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, num_pages)
    lo, hi = max(1, page - 2), min(num_pages, page + 2)
    return {
        "page": page, "per_page": per_page, "total": total, "num_pages": num_pages,
        "has_prev": page > 1, "has_next": page < num_pages, "prev": page - 1, "next": page + 1,
        "start": 0 if total == 0 else (page - 1) * per_page + 1,
        "end": min(page * per_page, total),
        "page_range": range(lo, hi + 1),
        "per_page_choices": PER_PAGE_CHOICES,
    }


@graph_view
def node_list(request):
    category = request.GET.get("category") or ""
    q = request.GET.get("q", "")
    page, per_page = _paginate(request)
    rows, total = gs.list_nodes(cat_slug=category or None, q=q, limit=per_page, offset=(page - 1) * per_page)
    params = {"per_page": per_page}
    if category:
        params["category"] = category
    if q:
        params["q"] = q
    return bento_render(request, "bento/node_list.html", {
        "nodes": rows,
        "categories": BentoCategory.objects.all(),
        "active_category": category,
        "query": q,
        "pagination": _page_context(page, per_page, total),
        "filter_qs": urlencode(params),
    })


def _flat_props(properties):
    """Node/edge props as a flat dict (the ``extra`` bag merged in)."""
    flat = {k: v for k, v in (properties or {}).items() if k != "extra"}
    flat.update((properties or {}).get("extra") or {})
    return flat


def _skeleton(category):
    """A starter JSON object for a new node, one key per schema field."""
    defaults = {"string": "", "text": "", "integer": 0, "float": 0.0,
                "boolean": False, "datetime": "", "json": {}}
    sk = {f["name"]: defaults.get(f.get("type"), "") for f in (category.property_schema or [])}
    return json.dumps(sk, indent=2) if sk else "{}"


def _edge_type_choices(node_category_slug, *, as_source):
    """Edge types valid for a node of this category, with the allowed categories
    for the *other* endpoint. ``as_source`` True → node is the edge source."""
    out = []
    for et in BentoEdgeType.objects.prefetch_related("allowed_sources", "allowed_targets"):
        my_side = et.allowed_sources if as_source else et.allowed_targets
        other_side = et.allowed_targets if as_source else et.allowed_sources
        my_slugs = list(my_side.values_list("slug", flat=True))
        if my_slugs and node_category_slug not in my_slugs:
            continue  # this node's category can't sit on this end
        out.append({
            "slug": et.slug, "name": et.name,
            "cats": list(other_side.values_list("slug", flat=True)),  # [] = any
        })
    return out


def _relations_context(uid, cat_slug):
    """Context for the Relations to/from panels (shared by detail + edit)."""
    edges, _total = gs.list_edges(node_uid=uid, limit=500, offset=0)
    return {
        "relations_to": [e for e in edges if e["source"] == uid],     # outgoing
        "relations_from": [e for e in edges if e["target"] == uid],   # incoming
        "to_edge_types": _edge_type_choices(cat_slug, as_source=True),
        "from_edge_types": _edge_type_choices(cat_slug, as_source=False),
    }


@graph_view
def node_detail(request, uid):
    node = gs.get_node(uid)
    return bento_render(request, "bento/node_detail.html", {
        "node": node,
        **_relations_context(uid, node["category_slug"]),
    })


@graph_view
def node_create(request):
    slug = request.GET.get("category") or request.POST.get("category")
    category = BentoCategory.objects.filter(slug=slug).first() if slug else None
    if not category:
        return bento_render(request, "bento/node_pick_category.html", {
            "categories": BentoCategory.objects.all(),
        })

    error = None
    if request.method == "POST":
        data_raw = request.POST.get("data", "")
        props, error = _parse_json_object(data_raw)
        if error is None:
            try:
                node = gs.create_node(category.slug, props)
                messages.success(request, _("Node created."))
                return redirect("bento:node_detail", uid=node["uid"])
            except (gs.GraphValidationError, ValueError) as exc:
                error = str(exc)
    else:
        data_raw = _skeleton(category)

    return bento_render(request, "bento/node_form.html", {
        "category": category, "data": data_raw, "error": error,
        "title": _("New %(name)s") % {"name": category.name},
    })


@graph_view
def node_update(request, uid):
    node = gs.get_node(uid)
    category = get_object_or_404(BentoCategory, slug=node["category_slug"])
    error = None

    if request.method == "POST":
        data_raw = request.POST.get("data", "")
        props, error = _parse_json_object(data_raw)
        if error is None:
            try:
                gs.update_node(uid, props)
                messages.success(request, _("Node updated."))
                return redirect("bento:node_detail", uid=uid)
            except (gs.GraphValidationError, ValueError) as exc:
                error = str(exc)
    else:
        flat = _flat_props(node["properties"])
        data_raw = json.dumps(flat, indent=2) if flat else "{}"

    return bento_render(request, "bento/node_form.html", {
        "category": category, "node": node, "data": data_raw, "error": error,
        "title": _("Edit node"),
        **_relations_context(uid, node["category_slug"]),
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
    et = request.GET.get("edge_type") or ""
    q = request.GET.get("q", "")
    page, per_page = _paginate(request)
    rows, total = gs.list_edges(et_slug=et or None, q=q, limit=per_page, offset=(page - 1) * per_page)
    params = {"per_page": per_page}
    if et:
        params["edge_type"] = et
    if q:
        params["q"] = q
    return bento_render(request, "bento/edge_list.html", {
        "edges": rows,
        "edge_types": BentoEdgeType.objects.all(),
        "active_edge_type": et,
        "query": q,
        "pagination": _page_context(page, per_page, total),
        "filter_qs": urlencode(params),
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
    origin = (request.POST.get("origin") or request.GET.get("origin") or "").strip()
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
                return redirect("bento:node_detail", uid=origin or from_uid)
            except (gs.GraphValidationError, gs.NotFound, ValueError) as exc:
                error = str(exc)
        # Posted from a node modal → surface the error on that node, no full page.
        if error and origin:
            messages.error(request, error)
            return redirect("bento:node_detail", uid=origin)

    nodes, _total = gs.list_nodes(limit=500)
    return bento_render(request, "bento/edge_form.html", {
        "edge_types": BentoEdgeType.objects.all(),
        "nodes": nodes,
        "selected": {"from_uid": from_uid, "to_uid": to_uid, "edge_type": et_slug,
                     "data": data_raw, "origin": origin},
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
