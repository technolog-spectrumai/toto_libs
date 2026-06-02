import json

from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.db import models as db_models

from toto.telegraph.api_views import CorsApiView
from toto.bento.models import Category, IdeaBox


def _category_to_dict(c):
    return {"id": c.id, "name": c.name, "slug": c.slug, "description": c.description}


def _box_to_dict(box):
    return {
        "id": box.id,
        "title": box.title,
        "body": box.body if not box.is_locked else "",
        "is_concept": box.is_concept,
        "is_locked": box.is_locked,
        "category_id": box.category_id,
        "category_name": box.category.name if box.category else None,
        "source_title": box.source_title,
        "source_url": box.source_url,
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
                db_models.Q(title__icontains=q) | db_models.Q(body__icontains=q)
            )
        return JsonResponse({"boxes": [_box_to_dict(b) for b in qs]})

    def post(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON."}, status=400)

        title = data.get("title", "").strip()
        body = data.get("body", "")
        is_concept = bool(data.get("is_concept", False))
        category_id = data.get("category_id")

        category = None
        if category_id:
            try:
                category = Category.objects.get(pk=category_id)
            except Category.DoesNotExist:
                return JsonResponse({"error": "Category not found."}, status=404)

        box = IdeaBox.objects.create(
            title=title,
            body=body,
            is_concept=is_concept,
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
        if "title" in data:
            box.title = data["title"]
            fields.append("title")
        if "body" in data:
            box.body = data["body"]
            fields.append("body")
        if "is_concept" in data:
            box.is_concept = bool(data["is_concept"])
            fields.append("is_concept")
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
