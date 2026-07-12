import json
import mimetypes
import os
from io import BytesIO
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.http import FileResponse, JsonResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views import View
from django.views.generic import TemplateView, DetailView, ListView
from django.urls import reverse, NoReverseMatch
from django.contrib.auth.mixins import LoginRequiredMixin

from django.contrib import messages
from django.utils.decorators import method_decorator
from toto.ui import PageProcessor
from .models import VaultFile, Bucket, FileGateway, VaultDirectory, BucketCopyLog
from .storage_backends import get_bucket_storage


# Empty-file-creatable types and their extension labels (keys mirror
# CreateEmptyFileView._INITIAL). A type is only offered/allowed in "New file" when an
# editor plugin is registered for it — so deployments missing an editor app (e.g. faros
# has no latex/notebook/neojson editor) neither show nor accept those types.
CREATABLE_TYPES = [
    ("text", ".txt"), ("json", ".json"), ("yaml", ".yaml"), ("xml", ".xml"),
    ("csv", ".csv"), ("html", ".html"), ("latex", ".tex"), ("bib", ".bib"),
    ("svg", ".svg"), ("neojson", ".neojson"),
    # Presentations (toto.memo), contracts (toto.notarius) and notebooks
    # (toto.mandragora) are ordinary .xml files now — created/edited via their own
    # apps (which content-sniff the XML root), not the vault "New file" menu.
]


def available_create_types():
    """[(type, ext), …] for creatable types that have a registered editor plugin."""
    from toto.vault.plugins import VaultEditorPlugin  # local: registry filled in ready()
    return [(t, ext) for t, ext in CREATABLE_TYPES if VaultEditorPlugin.for_file_type(t)]


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

    def _build_flat_items(self, dirs, files, dir_gateway_map, user_bucket_pks=None):
        from toto.vault.plugins import VaultPlayPlugin

        def _play_url_for(f):
            if f.is_encrypted:
                return ""
            plugin = VaultPlayPlugin.for_file_type(f.file_type)
            # A plugin whose target URL isn't mounted (its feature flag is off) must
            # not take down the whole listing — degrade to "no play link" instead.
            try:
                return plugin.get_play_url(f) if plugin else ""
            except NoReverseMatch:
                return ""

        from toto.vault.plugins import VaultEditorPlugin

        def _editor_url_for(f):
            # Encrypted files hold ciphertext — never editable. Blanking the URL hides
            # the "Open in editor" button in list, grid and the actions chooser at once
            # (mirrors _play_url_for above).
            if f.is_encrypted:
                return ""
            plugin = VaultEditorPlugin.for_file_type(f.file_type)
            try:
                return plugin.get_editor_url(f) if plugin else ""
            except NoReverseMatch:
                return ""

        try:
            from toto.fileservices.plugin import FileServicePlugin
            _fs_plugins = FileServicePlugin.all()
        except Exception:
            _fs_plugins = []

        def _has_services(f):
            return any(p.accepts(f) for p in _fs_plugins)

        # gitvault decorations (per-directory git actions), only when installed.
        # Shapes: repo root → {repo_pk, repo_name, urls}; inside a repo's
        # subtree → {"in": True}; repo-able → {init_url}; app off → None.
        def _git_info(d):
            return None

        from django.apps import apps as django_apps
        if django_apps.is_installed("toto.gitvault"):
            from toto.gitvault.integration import repo_urls
            from toto.gitvault.models import GitRepo

            _repos = {
                r.directory_id: r
                for r in GitRepo.objects.filter(
                    directory_id__in=[d.pk for d in dirs]
                ).select_related("directory")
            }
            _parent_of = {d.pk: d.parent_id for d in dirs}

            def _git_info(d):
                repo = _repos.get(d.pk)
                if repo:
                    return {
                        "repo_pk": repo.pk,
                        "repo_name": repo.directory.name,
                        "urls": repo_urls(repo),
                    }
                node = _parent_of.get(d.pk)
                while node is not None:
                    if node in _repos:
                        return {"in": True}
                    node = _parent_of.get(node)
                return {"init_url": reverse("gitvault:init", args=[d.pk])}

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
                    "bpk": d.bucket_id,
                    "n_files": n_files,
                    "n_dirs": n_dirs,
                    "locked": d.allowed_users.exists(),
                    "upload_url": dir_gateway_map.get(d.pk, ""),
                    "can_create": d.bucket_id in user_bucket_pks if user_bucket_pks else False,
                    "git": _git_info(d),
                })
                visit(d.pk, depth + 1)
                for f in sorted(files_by_dir.get(d.pk, []), key=lambda x: x.title):
                    _url = f.get_public_url() or ""
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
                        "url": _url if not f.is_encrypted else "",
                        "raw_url": _url,
                        "bpk": f.bucket_id,
                        "play_url": _play_url_for(f),
                        "editor_url": _editor_url_for(f),
                        "has_services": _has_services(f),
                    })

        visit(None, 0)

        for f in sorted(files_by_dir.get(None, []), key=lambda x: x.title):
            _url = f.get_public_url() or ""
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
                "url": _url if not f.is_encrypted else "",
                "raw_url": _url,
                "bpk": f.bucket_id,
                "play_url": _play_url_for(f),
                "editor_url": _editor_url_for(f),
                "has_services": _has_services(f),
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

        # Public files + the authenticated owner's own files (private files they
        # created/exported — e.g. .neojson graphs — must be visible to their owner,
        # not only public ones or encrypted-privates).
        if user.is_authenticated:
            visibility_q = Q(is_public=True) | Q(owner=user)
        else:
            visibility_q = Q(is_public=True)
        file_qs = VaultFile.objects.filter(visibility_q).distinct().select_related(
            "owner", "bucket", "directory"
        ).order_by("title")
        if bucket_slug:
            file_qs = file_qs.filter(bucket__slug=bucket_slug)

        _gw_direct = {gw.directory_id for gw in FileGateway.objects.only("directory_id")}
        _dirs_by_pk = {d.pk: d for d in accessible_dirs}

        def _find_gateway_dir(dir_pk):
            node_pk, seen = dir_pk, set()
            while node_pk is not None and node_pk not in seen:
                seen.add(node_pk)
                if node_pk in _gw_direct:
                    return node_pk
                node = _dirs_by_pk.get(node_pk)
                if node is None:
                    break
                node_pk = node.parent_id
            return None

        dir_gateway_map = {}
        for _d in accessible_dirs:
            _gw_pk = _find_gateway_dir(_d.pk)
            if _gw_pk is None:
                continue
            _gw_url = reverse("vault:gateway_page", kwargs={"dir_pk": _gw_pk})
            dir_gateway_map[_d.pk] = _gw_url if _gw_pk == _d.pk else f"{_gw_url}?target_dir={_d.pk}"

        user_bucket_pks = (
            set(Bucket.objects.filter(owner=self.request.user).values_list("pk", flat=True))
            if self.request.user.is_authenticated else set()
        )
        flat_items = self._build_flat_items(accessible_dirs, list(file_qs), dir_gateway_map, user_bucket_pks)

        context["flat_items"] = flat_items
        context["selected_bucket"] = bucket_slug
        context["total_files"] = sum(1 for i in flat_items if i["t"] == "file")
        context["total_dirs"] = sum(1 for i in flat_items if i["t"] == "dir")

        # Git actions (gitvault) — the template includes the shared git UI
        # partial only when the app is installed.
        from django.apps import apps as django_apps
        context["gitvault_enabled"] = django_apps.is_installed("toto.gitvault")

        # File-service (wand) endpoints — only when the fileservices app is installed.
        try:
            context["services_url_tpl"] = reverse("fileservices:services_for_file", kwargs={"file_pk": 0})
            context["run_service_url_tpl"] = reverse("fileservices:run_service", kwargs={"file_pk": 0})
            context["open_service_url_tpl"] = reverse("fileservices:open_primary", kwargs={"file_pk": 0})
        except Exception:
            context["services_url_tpl"] = ""
            context["run_service_url_tpl"] = ""
            context["open_service_url_tpl"] = ""

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
        # "New file" type pills — only types whose editor is installed on this deployment.
        context["create_file_types"] = available_create_types()

        # Zip is workflow-backed (Celery); only offer it where the engine exists.
        from django.apps import apps as _apps
        context["zip_enabled"] = _apps.is_installed("toto.workflows")

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
        if (
            not request.user.is_superuser
            and gateway.allowed_users.exists()
            and request.user not in gateway.allowed_users.all()
        ):
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

        target_dir = gateway.directory
        target_dir_id_str = self.request.GET.get("target_dir", "").strip()
        if target_dir_id_str:
            try:
                td = VaultDirectory.objects.get(pk=int(target_dir_id_str), bucket=gateway.bucket)
                target_dir = td
            except (VaultDirectory.DoesNotExist, ValueError):
                pass

        context["target_dir_path"] = get_full_path(target_dir)
        context["target_dir_id"] = target_dir.pk

        recent = VaultFile.objects.filter(
            directory=target_dir, owner=user
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

        if (
            not request.user.is_superuser
            and gateway.allowed_users.exists()
            and request.user not in gateway.allowed_users.all()
        ):
            return JsonResponse({"error": "You are not allowed to use this gateway"}, status=403)

        uploaded_files = request.FILES.getlist("file")
        if not uploaded_files:
            return JsonResponse({"error": "No file uploaded"}, status=400)

        target_directory_id = request.POST.get("target_directory_id", "").strip()
        if target_directory_id:
            try:
                directory = VaultDirectory.objects.get(pk=int(target_directory_id), bucket=gateway.bucket)
            except (VaultDirectory.DoesNotExist, ValueError):
                return JsonResponse({"error": "Invalid target directory."}, status=400)
        else:
            directory = gateway.directory

        valid_types = {code for code, _ in VaultFile.FILE_TYPES}
        manual_type = request.POST.get("file_type", "").strip()
        max_bytes = gateway.max_file_size * 1024

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

        from toto.quota import QuotaExceeded, check_quota, record_usage as _ru

        results, errors = [], []
        for uploaded_file in uploaded_files:
            # ── Per-file size limit ──────────────────────────────────────────
            if uploaded_file.size > max_bytes:
                errors.append(
                    f"{uploaded_file.name}: too large "
                    f"({uploaded_file.size / (1024*1024):.1f} MB; max {gateway.max_file_size / 1024:.1f} MB)."
                )
                continue

            # ── Per-file quota check ─────────────────────────────────────────
            if request.user.is_authenticated:
                try:
                    check_quota("vault", "storage.request", 1, "auth.User", str(request.user.pk))
                except QuotaExceeded as _exc:
                    errors.append(f"{uploaded_file.name}: {_exc}")
                    continue

            try:
                mime, _ = mimetypes.guess_type(uploaded_file.name)
                auto_file_type = VaultFile.detect_type(mime or "", uploaded_file.name)
                file_type = manual_type if manual_type in valid_types else auto_file_type

                vault_file = VaultFile(
                    owner=request.user,
                    title=uploaded_file.name,
                    # Assign a bucket-unique key up front so a colliding filename
                    # (re-upload, or two batch files slugging to the same key) gets
                    # suffixed -1/-2 instead of raising ValueError in save() — which
                    # would otherwise 500 the whole request with an HTML page and
                    # break the client's JSON parsing.
                    key=_unique_file_key(slugify(os.path.splitext(uploaded_file.name)[0]), gateway.bucket),
                    file=uploaded_file,
                    file_type=file_type,
                    bucket=gateway.bucket,
                    directory=directory,
                    is_public=gateway.make_public,
                )
                vault_file.save()
                vault_file.content_hash = vault_file.create_hash()
                vault_file.save()
            except Exception as _exc:  # noqa: BLE001 — one bad file mustn't 500 the batch
                errors.append(f"{uploaded_file.name}: {_exc}")
                continue

            # ── Record usage ─────────────────────────────────────────────────
            if request.user.is_authenticated:
                _uid = str(request.user.pk)
                _src = {"source_type": "vault.VaultFile", "source_id": str(vault_file.pk)}
                _ru("vault", "storage.request", 1, "auth.User", _uid,
                    idempotency_key=f"vault.upload.request:{vault_file.pk}", **_src)
                _size_mb = Decimal(str(vault_file.file_size_bytes or uploaded_file.size)) / Decimal("1048576")
                if _size_mb > 0:
                    _ru("vault", "storage.transfer_mb", _size_mb, "auth.User", _uid,
                        idempotency_key=f"vault.upload.transfer:{vault_file.pk}", **_src)

            results.append({
                "title": vault_file.title,
                "key": vault_file.key,
                "bucket": gateway.bucket.slug,
                "file_type": vault_file.file_type,
                "location": location,
                "public": vault_file.is_public,
                "public_url": vault_file.get_public_url(),
                "size": f"{uploaded_file.size / (1024*1024):.2f} MB",
            })

        # All files failed (e.g. every one over the limit) → surface as an error.
        if not results:
            return JsonResponse({"results": [], "errors": errors}, status=400)
        return JsonResponse({"results": results, "errors": errors})


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
            ).select_related("owner").order_by("name")
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

        from toto.quota import usage_summary
        context["quota_data"] = usage_summary(
            "vault", "auth.User", str(self.request.user.pk)
        )

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

        context["service_stats"] = self._service_stats(bucket)

        return PageProcessor().decorate(context, self.request)

    def _service_stats(self, bucket):
        """Per-service run counts + success/failure for this bucket's files."""
        try:
            from toto.fileservices.models import FileServiceRun
            from toto.fileservices.plugin import FileServicePlugin
        except Exception:
            return []

        rows = (
            FileServiceRun.objects.filter(bucket=bucket)
            .values("service_key", "status")
            .annotate(n=Count("id"))
        )
        agg = {}
        for r in rows:
            entry = agg.setdefault(r["service_key"], {"total": 0, "success": 0, "failed": 0, "outputs": 0})
            entry["total"] += r["n"]
            if r["status"] == FileServiceRun.SUCCESS:
                entry["success"] += r["n"]
            elif r["status"] == FileServiceRun.FAILED:
                entry["failed"] += r["n"]

        # Count produced output files per service.
        for run in FileServiceRun.objects.filter(bucket=bucket).only("service_key", "output_file_pks"):
            if run.service_key in agg:
                agg[run.service_key]["outputs"] += len(run.output_file_pks or [])

        result = []
        for key, data in sorted(agg.items(), key=lambda kv: -kv[1]["total"]):
            plugin = FileServicePlugin.get(key)
            result.append({
                "key": key,
                "title": plugin.get_title() if plugin else key,
                "icon": plugin.icon if plugin else "fa-solid fa-wand-magic-sparkles",
                **data,
            })
        return result


# ============================================================
# Copy Files
# ============================================================

def _unique_file_key(base_key, target_bucket):
    """A bucket-unique key for a new file: ``base_key`` (or ``file`` when empty),
    suffixed -1, -2, … until free. Avoids the ValueError VaultFile.save() raises when
    a slugified key already exists (different titles can slugify to the same key)."""
    base_key = base_key or "file"
    key = base_key
    counter = 1
    while VaultFile.objects.filter(bucket=target_bucket, key=key).exists():
        key = f"{base_key}-{counter}"
        counter += 1
    return key


def _unique_copy_key(source_file, target_bucket):
    return _unique_file_key(source_file.key or slugify(source_file.title), target_bucket)


def create_empty_vault_file(owner, bucket, directory, title, file_type):
    """Create + persist an empty editable vault file seeded with the type's starter
    content. Caller validates ownership and that file_type is creatable. Pre-assigns a
    bucket-unique key (see _unique_file_key) so colliding slugs don't raise."""
    from django.core.files.base import ContentFile
    vault_file = VaultFile(
        owner=owner,
        title=title,
        key=_unique_file_key(slugify(title), bucket),
        file_type=file_type,
        bucket=bucket,
        directory=directory,
        is_public=False,
    )
    vault_file.save()
    vault_file.file.save(title, ContentFile(CreateEmptyFileView._INITIAL[file_type].encode("utf-8")), save=True)
    vault_file.content_hash = vault_file.create_hash()
    vault_file.save()
    return vault_file


def resolve_new_file_target(user, bucket_id=None, directory_id=None):
    """Resolve the (bucket, directory) a user-chosen new file should land in.

    - Blank/None ``bucket_id`` -> the user's personal bucket (auto-created), root.
    - Otherwise the bucket must be owned by ``user`` (Http404 on miss).
    - Blank/None ``directory_id`` -> bucket root; otherwise the directory must
      belong to the resolved bucket (Http404 on miss).

    Enforces the same ownership rules as vault.api_views.FileCreateApiView so no
    app can create a file inside another user's bucket/folder.
    """
    if bucket_id in (None, "", 0, "0"):
        bucket, _ = Bucket.objects.get_or_create(
            owner=user,
            slug=f"personal-{user.username}",
            defaults={
                "name": f"Personal — {user.username}",
                "storage_backend": "local",
            },
        )
    else:
        bucket = get_object_or_404(Bucket, pk=bucket_id, owner=user)

    directory = None
    if directory_id not in (None, "", 0, "0"):
        directory = get_object_or_404(VaultDirectory, pk=directory_id, bucket=bucket)
    return bucket, directory


def new_file_picker_json(user):
    """(buckets_json, directories_json) for a bucket+directory "save-as" picker,
    scoped to the buckets/folders ``user`` may write to (their own). JSON shapes
    match the weather export modal: ``{id,name}`` and ``{id,bucket_id,path}``.
    Returns two empty-list JSON strings for anonymous users."""
    if not getattr(user, "is_authenticated", False):
        return "[]", "[]"
    buckets = Bucket.objects.filter(owner=user).order_by("name")
    directories = (
        VaultDirectory.objects.filter(owner=user)
        .select_related("bucket")
        .order_by("bucket__name", "name")
    )
    buckets_json = json.dumps([{"id": b.id, "name": b.name} for b in buckets])
    directories_json = json.dumps(
        [{"id": d.id, "bucket_id": d.bucket_id, "path": d.full_path()} for d in directories]
    )
    return buckets_json, directories_json


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

        src_driver = get_bucket_storage(source_bucket)
        dst_driver = get_bucket_storage(destination_bucket)

        with transaction.atomic():
            for source_file in selected_files:
                if copy_policy == "replace":
                    VaultFile.objects.filter(bucket=destination_bucket, key=source_file.key).delete()
                    key = source_file.key
                elif copy_policy == "fail":
                    key = source_file.key
                else:
                    key = _unique_copy_key(source_file, destination_bucket)

                content = src_driver.read(source_file.file.name)
                stored_name = dst_driver.save(source_file.file.name, content)

                new_file = VaultFile(
                    owner=source_file.owner,
                    title=source_file.title,
                    key=key,
                    content_hash=source_file.content_hash,
                    file_type=source_file.file_type,
                    is_encrypted=source_file.is_encrypted,
                    is_public=source_file.is_public,
                    notes=source_file.notes,
                    file_size_bytes=len(content),
                    bucket=destination_bucket,
                    directory=destination_directory,
                )
                new_file.file = stored_name
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


class EncryptFileView(LoginRequiredMixin, View):
    @staticmethod
    def _ensure_workflow():
        """Get-or-create the single-node 'vault-encrypt' workflow so encryption works
        even if ingress hasn't (re)seeded it on this deployment (mirrors CreateZipView)."""
        from toto.workflows.models import Workflow, WorkflowNode
        wf, created = Workflow.objects.get_or_create(
            slug="vault-encrypt",
            defaults={
                "name": "Encrypt file",
                "description": "Encrypt a vault file at rest. The password is supplied "
                               "out-of-band and never stored on the run.",
            },
        )
        if created or not wf.nodes.filter(task_name="vault_encrypt_file").exists():
            WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Encrypt file",
                task_name="vault_encrypt_file",
                position_x=0,
                position_y=0,
            )
        return wf

    def post(self, request):
        from django.apps import apps

        file_pk = request.POST.get("file_pk", "").strip()
        password = request.POST.get("password", "").strip()
        owner_password = request.POST.get("owner_password", "").strip() or None
        if not file_pk or not password:
            return JsonResponse({"ok": False, "error": "Missing required fields."}, status=400)
        vault_file = get_object_or_404(VaultFile, pk=file_pk, owner=request.user)
        if vault_file.is_encrypted:
            return JsonResponse({"ok": False, "error": "File is already encrypted."}, status=400)

        # Offload to a 'vault-encrypt' workflow run when the engine is installed
        # (faros + portal): encryption does an S3 download → crypto → re-upload that
        # can run for minutes, and a long synchronous request is dropped by Tor. The
        # password rides only as a transient Celery arg — it is NEVER written to the
        # run's input_data. The browser polls EncryptStatusView with the run id.
        # Ownership was just verified, so the task may trust file_pk.
        if getattr(settings, "VAULT_ENCRYPT_ASYNC", False) and apps.is_installed("toto.workflows"):
            run = None
            use_celery = False
            try:
                from toto.celery_utils import celery_available
                from toto.workflows.models import WorkflowRun

                wf = self._ensure_workflow()
                run = WorkflowRun.objects.create(
                    workflow=wf,
                    input_data={"data": {"file_pk": vault_file.pk, "owner_id": request.user.id}},
                )
                use_celery = celery_available()
            except Exception:  # noqa: BLE001 — engine/db issue → fall back to synchronous
                run = None
            if run is not None:
                from .tasks import encrypt_workflow_run
                try:
                    run_url = reverse("workflows:workflow_run_detail", args=[run.id])
                except Exception:
                    run_url = ""
                if use_celery:
                    try:
                        encrypt_workflow_run.delay(run.id, password, owner_password)
                    except Exception:  # noqa: BLE001 — broker down after all → run inline
                        encrypt_workflow_run(run.id, password, owner_password)
                else:
                    # No worker (dev/runserver): run inline; it self-handles errors.
                    encrypt_workflow_run(run.id, password, owner_password)
                return JsonResponse({
                    "ok": True, "async": True,
                    "workflow_run_id": run.id, "workflow_run_url": run_url,
                })

        try:
            vault_file.encrypt(password=password, owner_password=owner_password)
            vault_file.is_public = False
            vault_file.save(update_fields=["is_public"])
        except Exception as e:
            msg = str(e)
            if "EOF marker not found" in msg or "PdfRead" in type(e).__name__:
                msg = "File does not appear to be a valid PDF."
            return JsonResponse({"ok": False, "error": msg}, status=500)
        return JsonResponse({"ok": True, "raw_url": vault_file.get_public_url() or ""})


class EncryptStatusView(LoginRequiredMixin, View):
    """Poll a 'vault-encrypt' workflow run dispatched by EncryptFileView.

    Returns ``{status, is_terminal, done}`` plus ``{ok, raw_url, vault_file_id}`` /
    ``{ok, error}`` once terminal. The run is owner-bound via its input_data, and the
    result holds only the file's (already public) raw URL.
    """

    def get(self, request):
        run_id = request.GET.get("run_id", "").strip()
        if not run_id:
            return JsonResponse({"ok": False, "error": "Missing run_id."}, status=400)
        from toto.workflows.models import WorkflowRun

        run = get_object_or_404(WorkflowRun, pk=run_id)
        owner_id = ((run.input_data or {}).get("data") or {}).get("owner_id")
        if owner_id != request.user.id and not request.user.is_superuser:
            return JsonResponse({"ok": False, "error": "Not found."}, status=404)

        out = run.output_data or {}
        is_terminal = run.status in (WorkflowRun.COMPLETED, WorkflowRun.FAILED)
        payload = {"status": run.status, "is_terminal": is_terminal, "done": is_terminal}
        if run.status == WorkflowRun.COMPLETED:
            payload.update({"ok": True, "raw_url": out.get("raw_url", ""),
                            "vault_file_id": out.get("vault_file_id")})
        elif run.status == WorkflowRun.FAILED:
            payload.update({"ok": False, "error": out.get("error", "Encryption failed.")})
        return JsonResponse(payload)


class DecryptFileView(LoginRequiredMixin, View):
    def post(self, request):
        file_pk = request.POST.get("file_pk", "").strip()
        password = request.POST.get("password", "").strip()
        if not file_pk or not password:
            return JsonResponse({"ok": False, "error": "Missing required fields."}, status=400)
        vault_file = get_object_or_404(VaultFile, pk=file_pk, owner=request.user)
        if not vault_file.is_encrypted:
            return JsonResponse({"ok": False, "error": "File is not encrypted."}, status=400)
        try:
            vault_file.decrypt(password=password)
            vault_file.is_public = True
            vault_file.save(update_fields=["is_public"])
        except Exception as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=500)
        return JsonResponse({"ok": True, "url": vault_file.get_public_url() or ""})


class EncryptedDownloadView(LoginRequiredMixin, View):
    def post(self, request):
        file_pk  = request.POST.get("file_pk", "").strip()
        password = request.POST.get("password", "").strip()
        if not file_pk or not password:
            return JsonResponse({"ok": False, "error": "Missing required fields."}, status=400)
        try:
            vault_file = VaultFile.objects.select_related("owner", "bucket").get(
                pk=file_pk, owner=request.user
            )
        except VaultFile.DoesNotExist:
            return JsonResponse({"ok": False, "error": "File not found."}, status=404)
        if not vault_file.is_encrypted:
            return JsonResponse({"ok": False, "error": "File is not encrypted."}, status=400)
        try:
            data, content_type = vault_file.get_strategy().decrypt_to_bytes(vault_file, password=password)
        except Exception as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=400)
        filename = vault_file.title or os.path.basename(vault_file.file.name)
        return FileResponse(BytesIO(data), content_type=content_type, as_attachment=True, filename=filename)


class MoveFileView(LoginRequiredMixin, View):
    def post(self, request):
        file_pk = request.POST.get("file_pk", "").strip()
        dest_dir_pk = request.POST.get("destination_directory", "").strip()
        if not file_pk:
            return JsonResponse({"ok": False, "error": "Missing file_pk."}, status=400)
        vault_file = get_object_or_404(VaultFile, pk=file_pk, owner=request.user)
        if dest_dir_pk:
            dest_dir = get_object_or_404(VaultDirectory, pk=dest_dir_pk, bucket=vault_file.bucket)
            vault_file.directory = dest_dir
        else:
            vault_file.directory = None
        vault_file.save(update_fields=["directory"])
        return JsonResponse({"ok": True, "new_pid": vault_file.directory_id})


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

        src_driver = get_bucket_storage(source_bucket)
        dst_driver = get_bucket_storage(destination_bucket)

        with transaction.atomic():
            for source_file in selected_files:
                unique_key = _unique_copy_key(source_file, destination_bucket)
                content = src_driver.read(source_file.file.name)
                stored_name = dst_driver.save(source_file.file.name, content)
                new_file = VaultFile(
                    owner=source_file.owner,
                    title=source_file.title,
                    key=unique_key,
                    content_hash=source_file.content_hash,
                    file_type=source_file.file_type,
                    is_encrypted=source_file.is_encrypted,
                    is_public=source_file.is_public,
                    notes=source_file.notes,
                    file_size_bytes=len(content),
                    bucket=destination_bucket,
                )
                new_file.file = stored_name
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

# ============================================================
# Bucket connection URL + remote import
# ============================================================

class BucketConnectionUrlView(LoginRequiredMixin, View):
    """
    GET  /vault/buckets/<slug>/connection-url/
    Returns the credential-free connection URL for a bucket owned by the
    current user. Suitable for sharing with another toto instance.
    """

    def get(self, request, bucket_slug):
        bucket = get_object_or_404(Bucket, slug=bucket_slug, owner=request.user)
        from .connection import BucketConnectionSpec
        spec = BucketConnectionSpec.from_bucket(bucket)
        return JsonResponse({
            "url": spec.to_url(),
            "backend": spec.backend,
            "provider": spec.provider,
            "bucket_name": spec.bucket_name,
        })


class RemoteBucketImportView(LoginRequiredMixin, View):
    """
    POST /vault/buckets/import-remote/
    Body: {"url": "toto://other-server.example.com/vault/buckets/my-slug/",
           "name": "Optional display name"}

    Creates (or updates) a local Bucket that proxies to the remote toto
    server via RemoteTotoStorageDriver.  Only toto:// URLs are accepted —
    S3 buckets are configured directly via the admin.
    """

    def post(self, request):
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, TypeError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        raw_url = (data.get("url") or "").strip()
        name = (data.get("name") or "").strip()

        if not raw_url:
            return JsonResponse({"error": "'url' is required."}, status=400)

        from .connection import BucketConnectionSpec
        try:
            spec = BucketConnectionSpec.from_url(raw_url)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        if spec.backend != "remote_toto":
            return JsonResponse(
                {"error": "Only toto:// URLs are accepted. Configure S3 buckets via the admin."},
                status=400,
            )

        if not name:
            name = f"Remote: {spec.bucket_name}"

        slug = slugify(name)
        bucket, created = Bucket.objects.get_or_create(
            slug=slug,
            defaults={
                "name": name,
                "owner": request.user,
                "storage_backend": "remote_toto",
                "storage_config": spec.to_storage_config(),
            },
        )
        if not created:
            bucket.storage_backend = "remote_toto"
            bucket.storage_config = spec.to_storage_config()
            bucket.save(update_fields=["storage_backend", "storage_config"])

        return JsonResponse({"slug": bucket.slug, "created": created}, status=201 if created else 200)


@method_decorator(csrf_exempt, "dispatch")
class CreateEmptyFileView(LoginRequiredMixin, View):
    """Create an empty text-based vault file directly in a directory."""

    _ALLOWED = {"text", "json", "yaml", "xml", "csv", "html", "latex", "bib", "svg", "neojson"}
    _INITIAL = {
        "text":  "",
        "json":  "{}\n",
        "yaml":  "",
        "csv":   "",
        "xml":   '<?xml version="1.0" encoding="utf-8"?>\n<root>\n</root>\n',
        "html": (
            "<!DOCTYPE html>\n"
            '<html lang="en">\n'
            "<head>\n"
            '  <meta charset="utf-8">\n'
            "  <title></title>\n"
            "</head>\n"
            "<body>\n"
            "</body>\n"
            "</html>\n"
        ),
        # Starter .tex document. Mirrors toto.texlab.views.BLANK_TEX_DOCUMENT —
        # has a line of body so a freshly-created doc compiles to a PDF (an empty
        # body yields "No pages of output").
        "latex": (
            "\\documentclass{article}\n"
            "\n"
            "\\begin{document}\n"
            "\n"
            "Hello, \\LaTeX!\n"
            "\n"
            "\\end{document}\n"
        ),
        "bib":   "",
        "svg":   '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">\n</svg>\n',
        # Empty NeoJSON graph. Mirrors toto.ravioli.neojson.dumps(neojson.new_graph()).
        "neojson": (
            "{\n"
            '  "neojson": "1.0",\n'
            '  "type": "Graph",\n'
            '  "directed": true,\n'
            '  "nodes": [],\n'
            '  "relationships": [],\n'
            '  "metadata": {\n'
            '    "node_count": 0,\n'
            '    "relationship_count": 0,\n'
            '    "labels": [],\n'
            '    "relationship_types": []\n'
            "  }\n"
            "}\n"
        ),
    }

    def post(self, request):
        from toto.vault.plugins import VaultEditorPlugin

        title      = request.POST.get("title", "").strip()
        file_type  = request.POST.get("file_type", "").strip()
        dir_id     = request.POST.get("directory_id", "").strip()

        if not title:
            return JsonResponse({"error": "Filename is required."}, status=400)
        if file_type not in self._ALLOWED:
            return JsonResponse({"error": f"Unsupported type: {file_type}"}, status=400)
        # Only allow creating a type whose editor app is installed on this deployment
        # (the UI already hides the others; this guards direct POSTs).
        if VaultEditorPlugin.for_file_type(file_type) is None:
            return JsonResponse({"error": "No editor available for this file type."}, status=400)
        if not dir_id:
            return JsonResponse({"error": "directory_id is required."}, status=400)

        directory = get_object_or_404(VaultDirectory, pk=int(dir_id))
        if directory.bucket.owner != request.user:
            return JsonResponse({"error": "Permission denied."}, status=403)

        vault_file = create_empty_vault_file(
            request.user, directory.bucket, directory, title, file_type,
        )

        plugin = VaultEditorPlugin.for_file_type(file_type)
        editor_url = plugin.get_editor_url(vault_file) if plugin else None

        return JsonResponse({
            "status": "ok",
            "file_pk": vault_file.pk,
            "title": vault_file.title,
            "editor_url": editor_url,
        }, status=201)


class CreateZipView(LoginRequiredMixin, View):
    """Trigger a Celery-backed 'vault-zip' workflow that archives the selected
    files into a new .zip VaultFile. Only available when the workflow engine is
    installed (the Zip button is hidden otherwise)."""

    def post(self, request):
        from django.apps import apps
        if not apps.is_installed("toto.workflows"):
            return JsonResponse({"error": "Archiving is not available on this deployment."}, status=400)

        source_id = request.POST.get("source_directory_id", "").strip()
        target_id = request.POST.get("target_directory_id", "").strip()
        output_name = request.POST.get("output_name", "").strip()

        if not source_id:
            return JsonResponse({"error": "source_directory_id is required."}, status=400)
        source = get_object_or_404(VaultDirectory, pk=source_id)
        if source.bucket.owner_id != request.user.id and not request.user.is_superuser:
            return JsonResponse({"error": "Permission denied."}, status=403)

        target = None
        if target_id:
            target = get_object_or_404(VaultDirectory, pk=target_id, bucket=source.bucket)

        try:
            ids = [int(x) for x in request.POST.getlist("file_ids")]
        except (TypeError, ValueError):
            return JsonResponse({"error": "Invalid file selection."}, status=400)
        valid_ids = list(
            VaultFile.objects.filter(pk__in=ids, bucket=source.bucket, is_encrypted=False)
            .values_list("pk", flat=True)
        )
        if not valid_ids:
            return JsonResponse({"error": "Select at least one file to archive."}, status=400)

        payload = {"data": {
            "owner_id": request.user.id,
            "source_directory_id": source.pk,
            "target_directory_id": target.pk if target else None,
            "file_ids": valid_ids,
            "output_name": output_name,
        }}
        run, queued = self._start_zip_run(payload)

        try:
            run_url = reverse("workflows:workflow_run_detail", args=[run.id])
        except Exception:
            run_url = ""
        return JsonResponse({
            "status": "queued" if queued else "ok",
            "workflow_run_id": run.id,
            "workflow_run_url": run_url,
            "count": len(valid_ids),
        })

    @staticmethod
    def _ensure_workflow():
        """Get-or-create the single-node 'vault-zip' workflow so archiving works
        even if ingress hasn't (re)seeded it on this deployment."""
        from toto.workflows.models import Workflow, WorkflowNode
        wf, created = Workflow.objects.get_or_create(
            slug="vault-zip",
            defaults={
                "name": "Zip files",
                "description": "Bundle selected vault files into a single .zip archive saved back to the vault.",
            },
        )
        if created or not wf.nodes.filter(task_name="vault_zip_files").exists():
            WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Zip selected files",
                task_name="vault_zip_files",
                position_x=0,
                position_y=0,
            )
        return wf

    def _start_zip_run(self, payload):
        """Create the WorkflowRun and execute it. Uses Celery when a worker is
        reachable, otherwise runs inline (so zipping still works on a dev/runserver
        setup with no worker — mirrors fileservices.dispatch.dispatch_run)."""
        from toto.celery_utils import celery_available
        from toto.workflows.models import WorkflowRun

        wf = self._ensure_workflow()
        run = WorkflowRun.objects.create(workflow=wf, input_data=payload)
        if celery_available():
            from toto.workflows.tasks import start_workflow_run_task
            start_workflow_run_task.delay(run.id)
            return run, True

        from toto.workflows.services.executor import WorkflowExecutor
        WorkflowExecutor().start(run)
        return run, False


class ZipStatusView(LoginRequiredMixin, View):
    """Poll endpoint for a vault-zip workflow run."""

    def get(self, request):
        from django.apps import apps
        if not apps.is_installed("toto.workflows"):
            return JsonResponse({"error": "unavailable"}, status=400)
        from toto.workflows.models import WorkflowNodeRun, WorkflowRun

        run = get_object_or_404(WorkflowRun, pk=request.GET.get("run_id"))
        owner_id = ((run.input_data or {}).get("data") or {}).get("owner_id")
        if owner_id != request.user.id and not request.user.is_superuser:
            return JsonResponse({"error": "Not found."}, status=404)

        vfid = None
        if run.status == WorkflowRun.COMPLETED:
            for n in WorkflowNodeRun.objects.filter(workflow_run=run).order_by("-id"):
                d = (n.output_data or {}).get("data") or {}
                if d.get("vault_file_id"):
                    vfid = d["vault_file_id"]
                    break
        return JsonResponse({
            "status": run.status,
            "is_terminal": run.status in (WorkflowRun.COMPLETED, WorkflowRun.FAILED),
            "vault_file_id": vfid,
        })
