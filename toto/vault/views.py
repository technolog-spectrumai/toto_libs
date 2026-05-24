import mimetypes
from datetime import date, timedelta

from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.http import FileResponse, JsonResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView, DetailView
from django.urls import reverse
from django.contrib.auth.mixins import LoginRequiredMixin

from toto.ui import PageProcessor
from .models import VaultFile, Bucket, FileGateway, VaultDirectory


# ============================================================
# Public File Views
# ============================================================

class PublicFileListView(TemplateView):
    """
    Renders the full vault tree for all public files.
    Supports ?bucket=<slug> filter. Returns a flat item list
    consumed by the Alpine.js vaultTree() component.
    """
    template_name = "vault/public_file_list.html"

    def _build_flat_items(self, dirs, files):
        by_parent = {}
        for d in dirs:
            pid = d.parent_id
            if pid not in by_parent:
                by_parent[pid] = []
            by_parent[pid].append(d)

        files_by_dir = {}
        for f in files:
            did = f.directory_id
            if did not in files_by_dir:
                files_by_dir[did] = []
            files_by_dir[did].append(f)

        accessible_pks = {d.pk for d in dirs}
        flat = []

        def visit(parent_pk, depth):
            for d in sorted(by_parent.get(parent_pk, []), key=lambda x: x.name):
                n_files = len(files_by_dir.get(d.pk, []))
                n_dirs = sum(1 for c in by_parent.get(d.pk, []) if c.pk in accessible_pks)
                flat.append({
                    "t": "dir",
                    "id": d.pk,
                    "pid": parent_pk,
                    "depth": depth,
                    "name": d.name,
                    "bucket": d.bucket.name,
                    "n_files": n_files,
                    "n_dirs": n_dirs,
                    "locked": d.allowed_users.exists(),
                })
                visit(d.pk, depth + 1)
                for f in sorted(files_by_dir.get(d.pk, []), key=lambda x: x.title):
                    flat.append({
                        "t": "file",
                        "id": f.pk,
                        "pid": d.pk,
                        "depth": depth + 1,
                        "title": f.title,
                        "file_type": f.file_type,
                        "owner": f.owner.username,
                        "uploaded": f.uploaded_at.strftime("%Y-%m-%d"),
                        "encrypted": f.is_encrypted,
                        "url": f.get_public_url() or "",
                    })

        visit(None, 0)

        for f in sorted(files_by_dir.get(None, []), key=lambda x: x.title):
            flat.append({
                "t": "file",
                "id": f.pk,
                "pid": None,
                "depth": 0,
                "title": f.title,
                "file_type": f.file_type,
                "owner": f.owner.username,
                "uploaded": f.uploaded_at.strftime("%Y-%m-%d"),
                "encrypted": f.is_encrypted,
                "url": f.get_public_url() or "",
            })

        return flat

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        bucket_slug = self.request.GET.get("bucket", "")
        user = self.request.user

        dir_qs = VaultDirectory.objects.select_related(
            "bucket", "parent"
        ).prefetch_related("allowed_users")
        if bucket_slug:
            dir_qs = dir_qs.filter(bucket__slug=bucket_slug)
        accessible_dirs = [d for d in dir_qs if d.user_can_access(user)]

        file_qs = VaultFile.objects.filter(is_public=True).select_related(
            "owner", "bucket", "directory"
        ).order_by("title")
        if bucket_slug:
            file_qs = file_qs.filter(bucket__slug=bucket_slug)

        flat_items = self._build_flat_items(accessible_dirs, list(file_qs))

        context["flat_items"] = flat_items
        context["buckets"] = Bucket.objects.all()
        context["selected_bucket"] = bucket_slug
        context["total_files"] = sum(1 for i in flat_items if i["t"] == "file")
        context["total_dirs"] = sum(1 for i in flat_items if i["t"] == "dir")
        context["gateways"] = [
            {
                "name": gw.bucket.name,
                "url": reverse("vault:gateway_page", kwargs={"bucket_slug": gw.bucket.slug}),
            }
            for gw in FileGateway.objects.select_related("bucket").all()
        ]
        return PageProcessor().decorate(context, self.request)


class VaultFileDownloadView(View):
    """
    Download a file; respects public/private visibility.
    """
    def get(self, request, bucket_slug, key):
        file_obj = get_object_or_404(
            VaultFile.objects.select_related("bucket"),
            bucket__slug=bucket_slug,
            key=key
        )

        if file_obj.is_public:
            return FileResponse(
                file_obj.file.open(),
                as_attachment=True,
                filename=file_obj.file.name
            )

        if not request.user.is_authenticated:
            return HttpResponseForbidden("You must be logged in to access this file.")

        return FileResponse(
            file_obj.file.open(),
            as_attachment=True,
            filename=file_obj.file.name
        )


class FileGatewayPageView(LoginRequiredMixin, DetailView):
    model = FileGateway
    template_name = "vault/gateway.html"
    context_object_name = "gateway"

    def get_object(self):
        bucket_slug = self.kwargs.get("bucket_slug")
        return get_object_or_404(FileGateway, bucket__slug=bucket_slug)

    def get(self, request, *args, **kwargs):
        gateway = self.get_object()
        if gateway.allowed_users.exists() and request.user not in gateway.allowed_users.all():
            return HttpResponseForbidden("You are not allowed to access this gateway")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        gateway = self.get_object()
        user = self.request.user

        all_dirs = list(
            VaultDirectory.objects.filter(bucket=gateway.bucket)
            .prefetch_related("allowed_users")
        )
        all_dirs_by_pk = {d.pk: d for d in all_dirs}

        def get_full_path(d):
            parts = []
            node = d
            while node is not None:
                parts.append(node.name)
                node = all_dirs_by_pk.get(node.parent_id) if node.parent_id else None
            return "/".join(reversed(parts))

        accessible_dirs = [d for d in all_dirs if d.user_can_access(user)]
        context["directories"] = sorted(
            [{"id": d.pk, "label": get_full_path(d)} for d in accessible_dirs],
            key=lambda x: x["label"],
        )

        recent = VaultFile.objects.filter(
            bucket=gateway.bucket, owner=user
        ).select_related("directory").order_by("-uploaded_at")[:10]

        context["recent_uploads_list"] = [
            {
                "title": f.title,
                "file_type": f.file_type,
                "location": get_full_path(f.directory) if f.directory else "Root",
                "uploaded": f.uploaded_at.strftime("%Y-%m-%d %H:%M"),
                "public": f.is_public,
            }
            for f in recent
        ]

        return PageProcessor().decorate(context, self.request)


class FileGatewayUploadView(LoginRequiredMixin, View):
    """
    Handle uploads to a bucket through a gateway.
    """
    def post(self, request, bucket_slug):
        gateway = get_object_or_404(FileGateway, bucket__slug=bucket_slug)

        if gateway.allowed_users.exists() and request.user not in gateway.allowed_users.all():
            return JsonResponse({"error": "You are not allowed to use this gateway"}, status=403)

        if "file" not in request.FILES:
            return JsonResponse({"error": "No file uploaded"}, status=400)

        uploaded_file = request.FILES["file"]

        if uploaded_file.size > gateway.max_file_size * 1024:
            return JsonResponse({
                "error": f"File too large ({uploaded_file.size / (1024*1024):.1f} MB). Max is {gateway.max_file_size / 1024:.1f} MB."
            }, status=400)

        directory = None
        directory_id = request.POST.get("directory_id")
        if directory_id:
            directory = get_object_or_404(
                VaultDirectory.objects.prefetch_related("allowed_users"),
                pk=directory_id,
                bucket=gateway.bucket,
            )
            if not directory.user_can_access(request.user):
                return JsonResponse({"error": "You do not have access to this directory"}, status=403)

        mime, _ = mimetypes.guess_type(uploaded_file.name)
        file_type = VaultFile.detect_type(mime)

        vault_file = VaultFile(
            owner=request.user,
            title=uploaded_file.name,
            file=uploaded_file,
            file_type=file_type,
            bucket=gateway.bucket,
            directory=directory,
            is_public=gateway.make_public,
        )
        vault_file.save()
        vault_file.content_hash = vault_file.create_hash()
        vault_file.save()

        all_dirs = list(VaultDirectory.objects.filter(bucket=gateway.bucket))
        all_dirs_by_pk = {d.pk: d for d in all_dirs}

        def get_full_path(d):
            parts = []
            node = d
            while node is not None:
                parts.append(node.name)
                node = all_dirs_by_pk.get(node.parent_id) if node.parent_id else None
            return "/".join(reversed(parts))

        return JsonResponse({
            "result": {
                "title": vault_file.title,
                "key": vault_file.key,
                "bucket": gateway.bucket.slug,
                "file_type": vault_file.file_type,
                "location": get_full_path(directory) if directory else "Root",
                "public": vault_file.is_public,
                "public_url": vault_file.get_public_url(),
                "size": f"{uploaded_file.size / (1024*1024):.2f} MB",
            }
        })


# ============================================================
# Metrics / Statistics
# ============================================================

class VaultMetricsView(LoginRequiredMixin, TemplateView):
    """
    Aggregate statistics and charts for the vault: file counts by type,
    bucket breakdowns, upload activity over the last 30 days.
    """
    template_name = "vault/metrics.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # ── Top-level counters ──────────────────────────────
        total_buckets = Bucket.objects.count()
        total_dirs = VaultDirectory.objects.count()
        total_files = VaultFile.objects.count()
        public_files = VaultFile.objects.filter(is_public=True).count()
        encrypted_files = VaultFile.objects.filter(is_encrypted=True).count()
        week_ago = timezone.now() - timedelta(days=7)
        recent_count = VaultFile.objects.filter(uploaded_at__gte=week_ago).count()

        context.update({
            "total_buckets": total_buckets,
            "total_dirs": total_dirs,
            "total_files": total_files,
            "public_files": public_files,
            "encrypted_files": encrypted_files,
            "recent_count": recent_count,
        })

        # ── Chart data ──────────────────────────────────────
        context["files_by_type"] = list(
            VaultFile.objects.values("file_type")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        context["files_by_bucket"] = list(
            VaultFile.objects.filter(bucket__isnull=False)
            .values("bucket__name")
            .annotate(count=Count("id"))
            .order_by("-count")[:12]
        )

        thirty_days_ago = timezone.now() - timedelta(days=29)
        daily_qs = {
            entry["day"]: entry["count"]
            for entry in VaultFile.objects.filter(uploaded_at__gte=thirty_days_ago)
            .annotate(day=TruncDate("uploaded_at"))
            .values("day")
            .annotate(count=Count("id"))
        }
        today = date.today()
        context["daily_series"] = [
            {
                "date": (today - timedelta(days=29 - i)).strftime("%m-%d"),
                "count": daily_qs.get(today - timedelta(days=29 - i), 0),
            }
            for i in range(30)
        ]

        # ── Per-bucket breakdown ────────────────────────────
        context["bucket_stats"] = list(
            Bucket.objects.annotate(
                file_count=Count("files", distinct=True),
                dir_count=Count("directories", distinct=True),
                public_count=Count("files", filter=Q(files__is_public=True), distinct=True),
                encrypted_count=Count("files", filter=Q(files__is_encrypted=True), distinct=True),
            ).select_related("owner").order_by("name")
        )
        context["gateway_bucket_pks"] = set(
            FileGateway.objects.values_list("bucket_id", flat=True)
        )

        # ── Recent files ────────────────────────────────────
        context["recent_files"] = VaultFile.objects.select_related(
            "owner", "bucket", "directory"
        ).order_by("-uploaded_at")[:8]

        return PageProcessor().decorate(context, self.request)
