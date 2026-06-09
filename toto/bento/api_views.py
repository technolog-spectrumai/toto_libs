import json

from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.db import models as db_models

from toto.telegraph.api_views import CorsApiView
from toto.bento.models import Category, IdeaBox, IdeaLink


def _category_to_dict(c):
    return {"id": c.id, "name": c.name, "slug": c.slug, "description": c.description}


def _box_to_dict(box):
    properties = dict(box.properties)
    if box.is_locked:
        properties["body"] = ""
    return {
        "id": box.id,
        "label": box.label,
        "is_locked": box.is_locked,
        "category_id": box.category_id,
        "category_name": box.category.name if box.category else None,
        "properties": properties,
        "created_at": box.created_at.isoformat(),
        "updated_at": box.updated_at.isoformat(),
    }


@method_decorator(csrf_exempt, name="dispatch")
class BoxListApiView(CorsApiView):
    def get(self, request):
        qs = IdeaBox.objects.select_related("category").order_by("-created_at")
        q = request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                db_models.Q(label__icontains=q) | db_models.Q(properties__body__icontains=q)
            )
        return JsonResponse({"boxes": [_box_to_dict(b) for b in qs]})

    def post(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON."}, status=400)

        label = data.get("label", "").strip()
        properties = data.get("properties") or {}
        category_id = data.get("category_id")

        if not isinstance(properties, dict):
            return JsonResponse({"error": "properties must be an object."}, status=400)

        category = None
        if category_id:
            try:
                category = Category.objects.get(pk=category_id)
            except Category.DoesNotExist:
                return JsonResponse({"error": "Category not found."}, status=404)

        box = IdeaBox.objects.create(
            label=label,
            properties=properties,
            category=category,
        )
        return JsonResponse(_box_to_dict(box), status=201)


@method_decorator(csrf_exempt, name="dispatch")
class BoxDetailApiView(CorsApiView):
    def _get_box(self, pk):
        try:
            return IdeaBox.objects.select_related("category").get(pk=pk)
        except IdeaBox.DoesNotExist:
            return None

    def get(self, request, pk):
        box = self._get_box(pk)
        if not box:
            return JsonResponse({"error": "Not found."}, status=404)
        return JsonResponse(_box_to_dict(box))

    def patch(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        box = self._get_box(pk)
        if not box:
            return JsonResponse({"error": "Not found."}, status=404)
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON."}, status=400)

        fields = []
        if "label" in data:
            box.label = data["label"]
            fields.append("label")
        if "properties" in data:
            properties = data["properties"] or {}
            if not isinstance(properties, dict):
                return JsonResponse({"error": "properties must be an object."}, status=400)
            box.properties = properties
            fields.append("properties")
        if "category_id" in data:
            cid = data["category_id"]
            if cid is None:
                box.category = None
            else:
                try:
                    box.category = Category.objects.get(pk=cid)
                except Category.DoesNotExist:
                    return JsonResponse({"error": "Category not found."}, status=404)
            fields.append("category")

        if fields:
            fields.append("updated_at")
            box.save(update_fields=fields)
        return JsonResponse(_box_to_dict(box))

    def delete(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        box = self._get_box(pk)
        if not box:
            return JsonResponse({"error": "Not found."}, status=404)
        box.delete()
        return JsonResponse({}, status=204)


@method_decorator(csrf_exempt, name="dispatch")
class CategoryListApiView(CorsApiView):
    def get(self, request):
        cats = Category.objects.order_by("name")
        return JsonResponse({"categories": [_category_to_dict(c) for c in cats]})


def _link_to_dict(link):
    return {
        "id": link.id,
        "from_box": link.from_box_id,
        "to_box": link.to_box_id,
        "label": link.label or "related to",
        "from_box_label": link.from_box.label or "Untitled",
        "to_box_label": link.to_box.label or "Untitled",
    }


@method_decorator(csrf_exempt, name="dispatch")
class LinkListCreateApiView(CorsApiView):
    def get(self, request, pk=None):
        qs = IdeaLink.objects.select_related("from_box", "to_box").order_by("-created_at")
        if pk is not None:
            qs = qs.filter(db_models.Q(from_box_id=pk) | db_models.Q(to_box_id=pk))
        return JsonResponse({"links": [_link_to_dict(l) for l in qs[:200]]})

    def post(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON."}, status=400)

        from_id = data.get("from_box")
        to_id = data.get("to_box")
        label = data.get("label", "").strip()

        if not from_id or not to_id:
            return JsonResponse({"error": "from_box and to_box are required."}, status=400)
        if from_id == to_id:
            return JsonResponse({"error": "A box cannot link to itself."}, status=400)

        try:
            from_box = IdeaBox.objects.get(pk=from_id)
            to_box = IdeaBox.objects.get(pk=to_id)
        except IdeaBox.DoesNotExist:
            return JsonResponse({"error": "Box not found."}, status=404)

        link, created = IdeaLink.objects.get_or_create(
            from_box=from_box, to_box=to_box, label=label,
            defaults={}
        )
        return JsonResponse(_link_to_dict(link), status=201 if created else 200)


@method_decorator(csrf_exempt, name="dispatch")
class LinkDeleteApiView(CorsApiView):
    def delete(self, request, pk):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            link = IdeaLink.objects.get(pk=pk)
        except IdeaLink.DoesNotExist:
            return JsonResponse({"error": "Not found."}, status=404)
        link.delete()
        return JsonResponse({}, status=204)


@method_decorator(csrf_exempt, name="dispatch")
class FullGraphApiView(CorsApiView):
    """
    Return ALL boxes, ALL IdeaLinks, and ALL categories as a unified graph.

    Node types:
      "box"       — IdeaBox, id prefixed "b{id}"
      "category"  — Category, id prefixed "cat{id}"

    Edge types:
      "link"      — IdeaLink between two boxes
      "category"  — box → its category
    """
    def get(self, request):
        boxes = list(IdeaBox.objects.select_related("category").order_by("id"))
        links = list(IdeaLink.objects.all())
        categories = list(Category.objects.all())

        # category nodes (ellipses)
        cat_nodes = [
            {
                "id": f"cat{c.id}",
                "label": c.name,
                "node_type": "category",
            }
            for c in categories
        ]

        # box nodes (rounded rectangles)
        box_nodes = [
            {
                "id": f"b{b.id}",
                "real_id": b.id,
                "label": b.label or "Untitled",
                "node_type": "box",
            }
            for b in boxes
        ]

        # idea-link edges (box ↔ box)
        link_edges = [
            {
                "id": f"l{l.id}",
                "source": f"b{l.from_box_id}",
                "target": f"b{l.to_box_id}",
                "label": l.label or "related to",
                "edge_type": "link",
            }
            for l in links
        ]

        # category edges (box → category)
        cat_edges = [
            {
                "id": f"cl{b.id}",
                "source": f"b{b.id}",
                "target": f"cat{b.category_id}",
                "label": "",
                "edge_type": "category",
            }
            for b in boxes if b.category_id
        ]

        return JsonResponse({
            "nodes": box_nodes + cat_nodes,
            "edges": link_edges + cat_edges,
        })
