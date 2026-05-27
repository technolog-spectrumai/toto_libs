import json
import mimetypes
import os
from datetime import date, timedelta
from decimal import Decimal

from django.core.files.base import ContentFile as DjangoContentFile
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.http import FileResponse, JsonResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views import View
from django.views.generic import TemplateView, DetailView, ListView
from django.urls import reverse
from django.contrib.auth.mixins import LoginRequiredMixin

from django.contrib import messages
from toto.ui import PageProcessor
from .models import VaultFile, Bucket, FileGateway, VaultDirectory, BucketCopyLog


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

    def _build_flat_items(self, dirs, files, dir_gateway_map):
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
                    "upload_url": dir_gateway_map.get(d.pk, ""),
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

        dir_gateway_map = {
            gw.directory_id: reverse("vault:gateway_page", kwargs={"dir_pk": gw.directory_id})
            for gw in FileGateway.objects.only("directory_id")
        }

        flat_items = self._build_flat_items(accessible_dirs, list(file_qs), dir_gateway_map)

        context["flat_items"] = flat_items
        context["selected_bucket"] = bucket_slug
        context["total_files"] = sum(1 for i in flat_items if i["t"] == "file")
        context["total_dirs"] = sum(1 for i in flat_items if i["t"] == "dir")

        # Per-bucket quota usage so the template can show "X MB / Y MB" next to each bucket name.
        bucket_quota_info = {}
        buckets = list(Bucket.objects.all())
        if buckets:
            from django.db.models import Sum as _Sum
            usage_qs = (
                VaultFile.objects
                .filter(bucket__in=buckets)
                .values("bucket_id")
                .annotate(used_bytes=_Sum("file_size_bytes"))
            )
            usage_map = {row["bucket_id"]: row["used_bytes"] or 0 for row in usage_qs}
            for b in buckets:
                used_mb = round((usage_map.get(b.pk, 0)) / 1_048_576, 2)
                quota_mb = b.storage_quota_mb
                bucket_quota_info[b.pk] = {
                    "used_mb": used_mb,
                    "quota_mb": quota_mb,
                    "pct": min(round(used_mb / quota_mb * 100) if quota_mb else 0, 100),
                    "over": quota_mb is not None and used_mb > quota_mb,
                }
        context["buckets"] = buckets
        context["bucket_quota_info"] = bucket_quota_info

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
        return get_object_or_404(
            FileGateway.objects.select_related("directory__bucket", "bucket"),
            directory_id=self.kwargs["dir_pk"],
        )

    def get(self, request, *args, **kwargs):
        gateway = self.get_object()
        if gateway.allowed_users.exists() and request.user not in gateway.allowed_users.all():
            return HttpResponseForbidden("You are not allowed to access this gateway")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        gateway = self.get_object()
        user = self.request.user

        all_dirs = list(VaultDirectory.objects.filter(bucket=gateway.bucket))
        dirs_by_pk = {d.pk: d for d in all_dirs}

        def get_full_path(d):
            parts = []
            node = d
            while node is not None:
                parts.append(node.name)
                node = dirs_by_pk.get(node.parent_id) if node.parent_id else None
            return "/".join(reversed(parts))

        context["target_dir_path"] = get_full_path(gateway.directory)

        recent = VaultFile.objects.filter(
            directory=gateway.directory, owner=user
        ).select_related("directory").order_by("-uploaded_at")[:10]

        context["recent_uploads_list"] = [
            {
                "title": f.title,
                "file_type": f.file_type,
                "location": get_full_path(f.directory) if f.directory else "—",
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
    def post(self, request, dir_pk):
        gateway = get_object_or_404(
            FileGateway.objects.select_related("directory", "bucket"),
            directory_id=dir_pk,
        )

        if gateway.allowed_users.exists() and request.user not in gateway.allowed_users.all():
            return JsonResponse({"error": "You are not allowed to use this gateway"}, status=403)

        if "file" not in request.FILES:
            return JsonResponse({"error": "No file uploaded"}, status=400)

        uploaded_file = request.FILES["file"]

        if uploaded_file.size > gateway.max_file_size * 1024:
            return JsonResponse({
                "error": f"File too large ({uploaded_file.size / (1024*1024):.1f} MB). Max is {gateway.max_file_size / 1024:.1f} MB."
            }, status=400)

        directory = gateway.directory

        mime, _ = mimetypes.guess_type(uploaded_file.name)
        auto_file_type = VaultFile.detect_type(mime)
        valid_types = {code for code, _ in VaultFile.FILE_TYPES}
        manual_type = request.POST.get("file_type", "").strip()
        file_type = manual_type if manual_type in valid_types else auto_file_type

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

        if directory:
            all_dirs = list(VaultDirectory.objects.filter(bucket=gateway.bucket))
            dirs_by_pk = {d.pk: d for d in all_dirs}

            def get_full_path(d):
                parts = []
                node = d
                while node is not None:
                    parts.append(node.name)
                    node = dirs_by_pk.get(node.parent_id) if node.parent_id else None
                return "/".join(reversed(parts))

            location = get_full_path(directory)
        else:
            location = "Root"

        # ── Upload cost estimate from bucket tariff ────────────────────────
        upload_cost = None
        tariff = gateway.bucket.tariff if gateway.bucket_id else None
        if tariff:
            items = {
                item.metric.code: item
                for item in tariff.items.filter(active=True).select_related("metric", "charged_asset")
            }
            size_mb = Decimal(str(uploaded_file.size)) / Decimal("1048576")
            cost = Decimal("0")
            token = None
            req_item = items.get("storage.request")
            xfer_item = items.get("storage.transfer_mb")
            if req_item:
                cost += req_item.price_per_unit_display
                token = token or (req_item.charged_asset.unit_name if req_item.charged_asset else None)
            if xfer_item:
                cost += (size_mb * xfer_item.price_per_unit_display).quantize(Decimal("0.000001"))
                token = token or (xfer_item.charged_asset.unit_name if xfer_item.charged_asset else None)
            if token and cost > 0:
                upload_cost = f"{cost:.6f} {token}"

        return JsonResponse({
            "result": {
                "title": vault_file.title,
                "key": vault_file.key,
                "bucket": gateway.bucket.slug,
                "file_type": vault_file.file_type,
                "location": location,
                "public": vault_file.is_public,
                "public_url": vault_file.get_public_url(),
                "size": f"{uploaded_file.size / (1024*1024):.2f} MB",
                "upload_cost": upload_cost,
            },
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

        context["bucket_stats"] = list(
            Bucket.objects.annotate(
                file_count=Count("files", distinct=True),
                dir_count=Count("directories", distinct=True),
                public_count=Count("files", filter=Q(files__is_public=True), distinct=True),
                encrypted_count=Count("files", filter=Q(files__is_encrypted=True), distinct=True),
            ).select_related("owner", "tariff").order_by("name")
        )
        context["gateway_bucket_pks"] = set(
            FileGateway.objects.values_list("bucket_id", flat=True)
        )

        context["recent_files"] = VaultFile.objects.select_related(
            "owner", "bucket", "directory"
        ).order_by("-uploaded_at")[:8]

        # Copy flow data
        from django.db.models import Max
        context["copy_flows"] = list(
            BucketCopyLog.objects
            .filter(from_bucket__isnull=False, to_bucket__isnull=False)
            .values("from_bucket__name", "from_bucket__slug", "to_bucket__name", "to_bucket__slug")
            .annotate(total_files=Sum("file_count"), last_copy=Max("performed_at"))
            .order_by("-total_files")[:20]
        )

        thirty_days_ago_dt = timezone.now() - timedelta(days=29)
        copy_daily_qs = {
            entry["day"]: entry["count"]
            for entry in BucketCopyLog.objects
            .filter(performed_at__gte=thirty_days_ago_dt)
            .annotate(day=TruncDate("performed_at"))
            .values("day")
            .annotate(count=Sum("file_count"))
        }
        context["copy_daily_series"] = [
            {
                "date": (today - timedelta(days=29 - i)).strftime("%m-%d"),
                "count": copy_daily_qs.get(today - timedelta(days=29 - i), 0),
            }
            for i in range(30)
        ]

        return PageProcessor().decorate(context, self.request)


class BucketMetricsView(LoginRequiredMixin, TemplateView):
    """
    Per-bucket statistics: file type breakdown, directory breakdown,
    upload activity, and recent files.
    """
    template_name = "vault/bucket_metrics.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        bucket = get_object_or_404(Bucket, slug=self.kwargs["bucket_slug"])
        copy_files_qs = VaultFile.objects.filter(
            owner=self.request.user, bucket=bucket
        ).order_by("title")
        context["copy_files_data"] = [
            {"id": str(f.pk), "title": f.title, "file_type": f.file_type, "key": f.key or ""}
            for f in copy_files_qs
        ]
        context["dest_buckets"] = list(
            Bucket.objects.filter(owner=self.request.user).exclude(pk=bucket.pk).order_by("name")
        )

        total_files = VaultFile.objects.filter(bucket=bucket).count()
        total_dirs = VaultDirectory.objects.filter(bucket=bucket).count()
        public_files = VaultFile.objects.filter(bucket=bucket, is_public=True).count()
        encrypted_files = VaultFile.objects.filter(bucket=bucket, is_encrypted=True).count()
        root_files = VaultFile.objects.filter(bucket=bucket, directory__isnull=True).count()
        week_ago = timezone.now() - timedelta(days=7)
        recent_count = VaultFile.objects.filter(bucket=bucket, uploaded_at__gte=week_ago).count()

        gateways = list(bucket.gateways.select_related("directory").all())

        context.update({
            "bucket": bucket,
            "gateways": gateways,
            "total_files": total_files,
            "total_dirs": total_dirs,
            "public_files": public_files,
            "encrypted_files": encrypted_files,
            "root_files": root_files,
            "recent_count": recent_count,
        })

        context["files_by_type"] = list(
            VaultFile.objects.filter(bucket=bucket)
            .values("file_type")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        raw_by_dir = list(
            VaultFile.objects.filter(bucket=bucket)
            .values("directory__name")
            .annotate(count=Count("id"))
            .order_by("-count")[:12]
        )
        context["files_by_dir"] = [
            {"name": (d["directory__name"] or "Root"), "count": d["count"]}
            for d in raw_by_dir
        ]

        thirty_days_ago = timezone.now() - timedelta(days=29)
        daily_qs = {
            entry["day"]: entry["count"]
            for entry in VaultFile.objects.filter(
                bucket=bucket, uploaded_at__gte=thirty_days_ago
            )
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

        all_bucket_dirs = list(
            VaultDirectory.objects.filter(bucket=bucket)
            .prefetch_related("allowed_users")
            .annotate(
                file_count=Count("files", distinct=True),
                public_count=Count("files", filter=Q(files__is_public=True), distinct=True),
                encrypted_count=Count("files", filter=Q(files__is_encrypted=True), distinct=True),
            )
        )
        dirs_by_pk = {d.pk: d for d in all_bucket_dirs}

        def get_full_path(d):
            parts = []
            node = d
            while node is not None:
                parts.append(node.name)
                node = dirs_by_pk.get(node.parent_id) if node.parent_id else None
            return "/".join(reversed(parts))

        context["dir_stats"] = sorted(
            [
                {
                    "pk": d.pk,
                    "full_path": get_full_path(d),
                    "file_count": d.file_count,
                    "public_count": d.public_count,
                    "encrypted_count": d.encrypted_count,
                    "locked": d.allowed_users.exists(),
                }
                for d in all_bucket_dirs
            ],
            key=lambda x: x["full_path"],
        )

        quota_mb = bucket.storage_quota_mb
        raw_user_stats = list(
            VaultFile.objects.filter(bucket=bucket)
            .values("owner__id", "owner__username")
            .annotate(file_count=Count("id"), total_bytes=Sum("file_size_bytes"))
            .order_by("-total_bytes")
        )
        bucket_total_bytes = sum((row["total_bytes"] or 0) for row in raw_user_stats)
        bucket_total_mb = round(bucket_total_bytes / 1_048_576, 2)

        user_quota_rows = []
        for row in raw_user_stats:
            used_mb = round((row["total_bytes"] or 0) / 1_048_576, 2)
            pct = min(round(used_mb / quota_mb * 100) if quota_mb else 0, 100)
            user_quota_rows.append({
                "username": row["owner__username"],
                "file_count": row["file_count"],
                "used_mb": used_mb,
                "quota_mb": quota_mb,
                "pct": pct,
                "over": quota_mb is not None and used_mb > quota_mb,
            })
        context["user_quota_rows"] = user_quota_rows
        context["bucket_quota_mb"] = quota_mb
        context["bucket_total_mb"] = bucket_total_mb

        context["recent_files"] = VaultFile.objects.filter(bucket=bucket).select_related(
            "owner", "directory"
        ).order_by("-uploaded_at")[:8]

        context["bucket_tariff"] = bucket.tariff

        from toto.invoice.models import Invoice, InvoiceStatus
        context["bucket_invoices"] = list(
            Invoice.objects.filter(bucket=bucket)
            .select_related("issued_to", "issued_by")
            .order_by("-created_at")[:10]
        )
        context["bucket_pending_count"] = Invoice.objects.filter(
            bucket=bucket, status=InvoiceStatus.PENDING
        ).count()

        return PageProcessor().decorate(context, self.request)


# ============================================================
# Copy Files
# ============================================================

def _unique_copy_key(source_file, target_bucket):
    base_key = source_file.key or slugify(source_file.title) or "file"
    key = base_key
    counter = 1
    while VaultFile.objects.filter(bucket=target_bucket, key=key).exists():
        key = f"{base_key}-{counter}"
        counter += 1
    return key


class CopyFilesToBucketView(LoginRequiredMixin, View):
    template_name = "vault/copy_files.html"

    def _source_bucket(self, request, source_slug):
        return get_object_or_404(Bucket, slug=source_slug, owner=request.user)

    @staticmethod
    def _build_source_tree(request, source_bucket):
        dirs = list(
            VaultDirectory.objects.filter(bucket=source_bucket)
            .prefetch_related("allowed_users")
            .order_by("name")
        )
        files = list(
            VaultFile.objects.filter(bucket=source_bucket, owner=request.user).order_by("title")
        )
        by_parent = {}
        for d in dirs:
            by_parent.setdefault(d.parent_id, []).append(d)
        files_by_dir = {}
        for f in files:
            files_by_dir.setdefault(f.directory_id, []).append(f)
        accessible_pks = {d.pk for d in dirs}
        flat = []

        def _file_item(f, depth):
            return {
                "t": "file", "id": str(f.pk),
                "pid": str(f.directory_id) if f.directory_id else None,
                "depth": depth, "title": f.title, "file_type": f.file_type, "key": f.key,
            }

        def visit(parent_pk, depth):
            for d in sorted(by_parent.get(parent_pk, []), key=lambda x: x.name):
                n_files = len(files_by_dir.get(d.pk, []))
                n_dirs = sum(1 for c in by_parent.get(d.pk, []) if c.pk in accessible_pks)
                flat.append({
                    "t": "dir", "id": str(d.pk),
                    "pid": str(d.parent_id) if d.parent_id else None,
                    "depth": depth, "name": d.name,
                    "n_files": n_files, "n_dirs": n_dirs,
                })
                visit(d.pk, depth + 1)
                for f in sorted(files_by_dir.get(d.pk, []), key=lambda x: x.title):
                    flat.append(_file_item(f, depth + 1))

        visit(None, 0)
        for f in sorted(files_by_dir.get(None, []), key=lambda x: x.title):
            flat.append(_file_item(f, 0))
        return flat

    @staticmethod
    def _build_dest_tree(request, source_bucket):
        buckets = list(
            Bucket.objects.filter(owner=request.user).exclude(pk=source_bucket.pk).order_by("name")
        )
        bucket_pks = [b.pk for b in buckets]
        all_dirs = list(VaultDirectory.objects.filter(bucket__in=bucket_pks).order_by("name"))
        dirs_by_bucket = {}
        for d in all_dirs:
            dirs_by_bucket.setdefault(d.bucket_id, []).append(d)
        by_parent_bucket = {}
        for d in all_dirs:
            by_parent_bucket.setdefault((d.parent_id, d.bucket_id), []).append(d)
        flat = []

        def visit_dest(parent_id, depth, bucket_pk):
            pid_val = str(parent_id) if parent_id else f"b{bucket_pk}"
            for d in sorted(by_parent_bucket.get((parent_id, bucket_pk), []), key=lambda x: x.name):
                flat.append({
                    "t": "dir", "id": str(d.pk), "bpk": d.bucket_id,
                    "pid": pid_val, "depth": depth, "name": d.name,
                })
                visit_dest(d.pk, depth + 1, bucket_pk)

        for bucket in buckets:
            flat.append({
                "t": "bucket_root", "id": f"b{bucket.pk}", "bpk": bucket.pk,
                "pid": None, "depth": 0, "name": bucket.name,
                "n_dirs": len(dirs_by_bucket.get(bucket.pk, [])),
            })
            visit_dest(None, 1, bucket.pk)
        return flat

    def _build_context(self, request, source_bucket, form=None):
        from .forms import CopyFilesForm
        context = {
            "source_bucket": source_bucket,
            "form": form or CopyFilesForm(request.user, source_bucket),
            "source_items": self._build_source_tree(request, source_bucket),
            "dest_items": self._build_dest_tree(request, source_bucket),
        }
        return PageProcessor().decorate(context, request)

    def get(self, request, source_slug):
        source_bucket = self._source_bucket(request, source_slug)
        return render(request, self.template_name, self._build_context(request, source_bucket))

    def post(self, request, source_slug):
        from .forms import CopyFilesForm
        source_bucket = self._source_bucket(request, source_slug)
        form = CopyFilesForm(request.user, source_bucket, request.POST)
        if not form.is_valid():
            return render(request, self.template_name, self._build_context(request, source_bucket, form))

        destination_bucket = form.cleaned_data["destination_bucket"]
        selected_files = list(form.cleaned_data["files"])

        dest_dir_id = request.POST.get("destination_directory", "").strip()
        destination_directory = None
        if dest_dir_id:
            try:
                destination_directory = VaultDirectory.objects.get(
                    pk=dest_dir_id, bucket=destination_bucket
                )
            except VaultDirectory.DoesNotExist:
                form.add_error(None, "Invalid destination directory.")
                return render(request, self.template_name, self._build_context(request, source_bucket, form))

        copy_policy = request.POST.get("copy_policy", "add_suffix")
        if copy_policy not in ("replace", "fail", "add_suffix"):
            copy_policy = "add_suffix"

        if copy_policy == "fail":
            conflicts = [
                f.key for f in selected_files
                if VaultFile.objects.filter(bucket=destination_bucket, key=f.key).exists()
            ]
            if conflicts:
                preview = ", ".join(f'"{k}"' for k in conflicts[:5])
                if len(conflicts) > 5:
                    preview += f" … (+{len(conflicts) - 5} more)"
                form.add_error(None, f"Key conflict(s): {preview}")
                return render(request, self.template_name, self._build_context(request, source_bucket, form))

        with transaction.atomic():
            for source_file in selected_files:
                if copy_policy == "replace":
                    VaultFile.objects.filter(bucket=destination_bucket, key=source_file.key).delete()
                    key = source_file.key
                elif copy_policy == "fail":
                    key = source_file.key
                else:
                    key = _unique_copy_key(source_file, destination_bucket)

                source_file.file.open("rb")
                try:
                    content = source_file.file.read()
                finally:
                    source_file.file.close()

                new_file = VaultFile(
                    owner=source_file.owner,
                    title=source_file.title,
                    key=key,
                    content_hash=source_file.content_hash,
                    file_type=source_file.file_type,
                    is_encrypted=source_file.is_encrypted,
                    is_public=source_file.is_public,
                    notes=source_file.notes,
                    file_size_bytes=source_file.file_size_bytes,
                    bucket=destination_bucket,
                    directory=destination_directory,
                )
                orig_name = os.path.basename(source_file.file.name)
                new_file.file.save(orig_name, DjangoContentFile(content), save=False)
                new_file.save()

        count = len(selected_files)
        BucketCopyLog.objects.create(
            from_bucket=source_bucket,
            to_bucket=destination_bucket,
            performed_by=request.user,
            file_count=count,
        )
        messages.success(
            request,
            f"Copied {count} file{'s' if count != 1 else ''} to \"{destination_bucket.name}\".",
        )
        return redirect("vault:bucket_metrics", bucket_slug=destination_bucket.slug)


class RenameFileView(LoginRequiredMixin, View):
    _VALID_TYPES = {k for k, _ in VaultFile.FILE_TYPES}

    def post(self, request):
        file_pk   = request.POST.get("file_pk", "").strip()
        new_title = request.POST.get("title", "").strip()
        file_type = request.POST.get("file_type", "").strip()
        if not file_pk or not new_title:
            return JsonResponse({"ok": False, "error": "Missing required fields."}, status=400)
        if file_type and file_type not in self._VALID_TYPES:
            return JsonResponse({"ok": False, "error": "Invalid file type."}, status=400)
        vault_file = get_object_or_404(VaultFile, pk=file_pk, owner=request.user)
        vault_file.title = new_title
        update_fields = ["title"]
        if file_type and file_type != vault_file.file_type:
            vault_file.file_type = file_type
            update_fields.append("file_type")
        vault_file.save(update_fields=update_fields)
        return JsonResponse({"ok": True, "title": vault_file.title, "file_type": vault_file.file_type})


class DeleteFileView(LoginRequiredMixin, View):
    def post(self, request):
        file_pk = request.POST.get("file_pk", "").strip()
        if not file_pk:
            return JsonResponse({"ok": False, "error": "Missing file_pk."}, status=400)
        vault_file = get_object_or_404(VaultFile, pk=file_pk, owner=request.user)
        vault_file.file.delete(save=False)
        vault_file.delete()
        return JsonResponse({"ok": True})


class BucketCopyAjaxView(LoginRequiredMixin, View):
    """
    JSON endpoint used by the inline copy modal on the bucket metrics page.
    Accepts POST: files[] (IDs) + destination_bucket (ID).
    Returns {"ok": true, "count": N, "dest_slug": "...", "dest_name": "...", "dest_url": "..."}
    or {"ok": false, "error": "..."}.
    """
    def post(self, request, source_slug):
        source_bucket = get_object_or_404(Bucket, slug=source_slug, owner=request.user)

        file_ids = request.POST.getlist("files")
        dest_bucket_id = request.POST.get("destination_bucket", "").strip()

        if not file_ids:
            return JsonResponse({"ok": False, "error": "Select at least one file."}, status=400)
        if not dest_bucket_id:
            return JsonResponse({"ok": False, "error": "Choose a destination bucket."}, status=400)

        try:
            destination_bucket = Bucket.objects.get(pk=dest_bucket_id, owner=request.user)
        except Bucket.DoesNotExist:
            return JsonResponse({"ok": False, "error": "Invalid destination bucket."}, status=400)

        if destination_bucket.pk == source_bucket.pk:
            return JsonResponse({"ok": False, "error": "Source and destination must differ."}, status=400)

        selected_files = list(
            VaultFile.objects.filter(pk__in=file_ids, bucket=source_bucket, owner=request.user)
        )
        if len(selected_files) != len(file_ids):
            return JsonResponse({"ok": False, "error": "Some selected files are invalid."}, status=400)

        with transaction.atomic():
            for source_file in selected_files:
                unique_key = _unique_copy_key(source_file, destination_bucket)
                source_file.file.open("rb")
                try:
                    content = source_file.file.read()
                finally:
                    source_file.file.close()
                new_file = VaultFile(
                    owner=source_file.owner,
                    title=source_file.title,
                    key=unique_key,
                    content_hash=source_file.content_hash,
                    file_type=source_file.file_type,
                    is_encrypted=source_file.is_encrypted,
                    is_public=source_file.is_public,
                    notes=source_file.notes,
                    file_size_bytes=source_file.file_size_bytes,
                    bucket=destination_bucket,
                )
                orig_name = os.path.basename(source_file.file.name)
                new_file.file.save(orig_name, DjangoContentFile(content), save=False)
                new_file.save()

        count = len(selected_files)
        BucketCopyLog.objects.create(
            from_bucket=source_bucket,
            to_bucket=destination_bucket,
            performed_by=request.user,
            file_count=count,
        )
        return JsonResponse({
            "ok": True,
            "count": count,
            "dest_slug": destination_bucket.slug,
            "dest_name": destination_bucket.name,
            "dest_url": reverse("vault:bucket_metrics", kwargs={"bucket_slug": destination_bucket.slug}),
        })


# ============================================================
# Invoices
# ============================================================

class GenerateInvoiceView(LoginRequiredMixin, View):
    """
    GET: shows a pre-filled invoice form based on the bucket's tariff + current storage.
    POST: creates an invoice.Invoice and redirects to invoice list.
    """
    template_name = "vault/generate_invoice.html"

    def _bucket(self, bucket_slug):
        return get_object_or_404(
            Bucket.objects.select_related("tariff", "owner"),
            slug=bucket_slug,
        )

    def _estimate(self, bucket):
        """Return (amount, currency, usage_mb, item_or_None) from tariff + storage."""
        tariff = bucket.tariff
        if not tariff:
            return Decimal("0.00"), "TOKEN", 0.0, None

        usage_bytes = VaultFile.objects.filter(bucket=bucket).aggregate(
            total=Sum("file_size_bytes")
        )["total"] or 0
        usage_mb = round(usage_bytes / 1_048_576, 4)

        item = (
            tariff.items.filter(active=True)
            .select_related("metric", "charged_asset", "unit")
            .filter(metric__code__icontains="mb")
            .first()
        ) or tariff.items.filter(active=True).select_related("metric", "charged_asset", "unit").first()

        currency = item.charged_asset.unit_name if (item and item.charged_asset) else "TOKEN"
        if item:
            hours_per_month = Decimal("730")
            amount = (Decimal(str(usage_mb)) * item.price_per_unit_display * hours_per_month).quantize(Decimal("0.01"))
        else:
            amount = Decimal("0.00")
        return amount, currency, usage_mb, item

    def get(self, request, bucket_slug):
        bucket = self._bucket(bucket_slug)
        tariff = bucket.tariff
        if not tariff:
            messages.warning(request, "This bucket has no tariff assigned.")
            return redirect("vault:bucket_metrics", bucket_slug=bucket_slug)

        amount, currency, usage_mb, item = self._estimate(bucket)
        items = list(tariff.items.filter(active=True).select_related("metric", "charged_asset", "unit"))
        month = date.today().strftime("%B %Y")

        context = {
            "bucket": bucket,
            "tariff": tariff,
            "tariff_items": items,
            "usage_mb": usage_mb,
            "suggested_title": f"Storage Invoice — {bucket.name} — {month}",
            "suggested_amount": amount,
            "suggested_currency": currency,
        }
        return render(request, self.template_name, PageProcessor().decorate(context, request))

    def post(self, request, bucket_slug):
        from toto.invoice.models import Invoice as InvoiceModel

        bucket = self._bucket(bucket_slug)
        if not bucket.tariff:
            messages.error(request, "This bucket has no tariff assigned.")
            return redirect("vault:bucket_metrics", bucket_slug=bucket_slug)

        amount, currency, usage_mb, _ = self._estimate(bucket)
        month = date.today().strftime("%B %Y")
        title = f"Storage Invoice — {bucket.name} — {month}"
        description = f"Storage billing for {bucket.name} ({bucket.tariff.code}). {usage_mb} MB used."

        inv = InvoiceModel.objects.create(
            issued_to=bucket.owner,
            issued_by=request.user,
            bucket=bucket,
            title=title,
            description=description,
            amount=amount,
            currency_label=currency,
        )
        messages.success(request, f"Invoice '{inv.title}' generated.")
        return redirect("invoice:invoice_list")
