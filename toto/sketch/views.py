from django.core.files.base import ContentFile
from django.views.generic import ListView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from toto.ui import PageProcessor
import json
import uuid
from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from toto.sketch.models import Board, BoardObject
from django.utils.decorators import method_decorator
from toto.vault.models import VaultFile, Bucket


class BoardListView(LoginRequiredMixin, ListView):
    model = Board
    template_name = "sketch/board_list.html"
    context_object_name = "boards"
    login_url = reverse_lazy("core:login")

    def get_queryset(self):
        # Only boards owned by the logged-in user
        return Board.objects.filter(owner=self.request.user).order_by("name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["boards_json"] = json.dumps([
            {"id": b.id, "name": b.name, "created_at": b.created_at.strftime("%Y-%m-%d %H:%M")}
            for b in context["boards"]
        ])
        buckets = Bucket.objects.filter(owner=self.request.user).order_by("name")
        context["buckets_json"] = json.dumps([
            {"id": b.id, "name": b.name} for b in buckets
        ])
        return PageProcessor().decorate(context, self.request)


class BoardDetailView(LoginRequiredMixin, DetailView):
    model = Board
    template_name = "sketch/board_detail.html"
    context_object_name = "board"
    pk_url_kwarg = "board_id"   # URL will use <board_id>
    login_url = reverse_lazy("core:login")

    def get_object(self, queryset=None):
        # Ensure board belongs to the logged-in user
        return get_object_or_404(
            Board,
            id=self.kwargs["board_id"],
            owner=self.request.user
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        board = self.get_object()

        # QuerySet → list of dicts (JSON‑serializable)
        objects_qs = board.board_objects.filter(is_deleted=False).order_by("created_at")

        context["objects"] = json.dumps([
            {
                "object_id": obj.object_id,
                "object_type": obj.object_type,
                "data": obj.data
            }
            for obj in objects_qs
        ])
        context["background_image_url"] = (
            board.image.url if board.image else None
        )
        context["bucketName"] = board.bucket.name if board.bucket else ""
        return PageProcessor().decorate(context, self.request)


@method_decorator(csrf_exempt, name="dispatch")
class BoardSaveView(LoginRequiredMixin, View):

    def post(self, request, board_id):
        # Ensure board belongs to the logged-in user
        board = get_object_or_404(Board, id=board_id, owner=request.user)

        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        objects = payload.get("objects", [])

        incoming_ids = {obj["object_id"] for obj in objects}

        # Mark deleted objects
        BoardObject.objects.filter(board=board).exclude(object_id__in=incoming_ids).update(is_deleted=True)

        # Upsert objects
        for obj in objects:
            BoardObject.objects.update_or_create(
                board=board,
                object_id=obj["object_id"],
                defaults={
                    "object_type": obj["object_type"],
                    "data": obj["data"],
                    "is_deleted": False,
                }
            )

        return JsonResponse({"status": "ok", "saved": len(objects)})


@method_decorator(csrf_exempt, name="dispatch")
class BoardExportSVGView(LoginRequiredMixin, View):

    def post(self, request, board_id):
        board = get_object_or_404(Board, id=board_id, owner=request.user)

        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        svg_data = payload.get("svg")
        if not svg_data:
            return JsonResponse({"error": "Missing SVG data"}, status=400)

        if not board.bucket:
            return JsonResponse({"error": "Board has no assigned bucket"}, status=400)

        svg_bytes = svg_data.encode("utf-8")
        filename = payload.get("filename") or f"{board.name}-export.svg"

        vault_file = VaultFile.objects.create(
            owner=request.user,
            title=f"{board.name} Export",
            file_type="svg",     # <-- CORRECT TYPE
            bucket=board.bucket,
            is_public=True,
        )

        vault_file.file.save(filename, ContentFile(svg_bytes))
        vault_file.content_hash = vault_file.create_hash()
        vault_file.save()

        return JsonResponse({
            "status": "ok",
            "filename": filename,
            "file_id": vault_file.id,
            "file_url": vault_file.get_public_url(),
            "bucket": board.bucket.slug,
        })


@method_decorator(csrf_exempt, name="dispatch")
class BoardCreateView(LoginRequiredMixin, View):

    def post(self, request):
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        name = (payload.get("name") or "").strip()
        if not name:
            return JsonResponse({"error": "Name is required"}, status=400)

        bucket_id = payload.get("bucket_id")
        if not bucket_id:
            return JsonResponse({"error": "Bucket is required"}, status=400)

        bucket = get_object_or_404(Bucket, id=bucket_id, owner=request.user)

        board = Board.objects.create(
            id=uuid.uuid4().hex[:12],
            name=name,
            owner=request.user,
            bucket=bucket,
        )
        return JsonResponse({"status": "ok", "board_id": board.id, "name": board.name}, status=201)


@method_decorator(csrf_exempt, name="dispatch")
class BoardDeleteView(LoginRequiredMixin, View):

    def post(self, request, board_id):
        board = get_object_or_404(Board, id=board_id, owner=request.user)
        board.delete()
        return JsonResponse({"status": "ok"})

