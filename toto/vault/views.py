import mimetypes
from datetime import date, timedelta

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.http import FileResponse, JsonResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView, DetailView
from django.urls import reverse
from django.contrib.auth.mixins import LoginRequiredMixin

from django.contrib import messages
from toto.ui import PageProcessor
from .models import StorageAccount, VaultFile, Bucket, FileGateway, VaultDirectory


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

        # Map directory_pk → gateway page URL (directory is always set now)
        dir_gateway_map = {
            gw.directory_id: reverse("vault:gateway_page", kwargs={"dir_pk": gw.directory_id})
            for gw in FileGateway.objects.only("directory_id")
        }

        flat_items = self._build_flat_items(accessible_dirs, list(file_qs), dir_gateway_map)

        context["flat_items"] = flat_items
        context["buckets"] = Bucket.objects.all()
        context["selected_bucket"] = bucket_slug
        context["total_files"] = sum(1 for i in flat_items if i["t"] == "file")
        context["total_dirs"] = sum(1 for i in flat_items if i["t"] == "dir")

        context["bucket_billing"] = None
        if bucket_slug:
            try:
                from toto.vault.billing import get_bucket_billing_summary
                selected_bucket_obj = Bucket.objects.filter(slug=bucket_slug).first()
                if selected_bucket_obj:
                    context["bucket_billing"] = get_bucket_billing_summary(selected_bucket_obj, user=user)
            except Exception:
                pass

        context["has_storage_account"] = (
            user.is_authenticated and
            StorageAccount.objects.filter(user=user, active=True).exists()
        )

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

        try:
            from toto.vault.billing import get_bucket_billing_summary
            context["bucket_billing"] = get_bucket_billing_summary(gateway.bucket, user=user)
        except Exception:
            context["bucket_billing"] = None

        context["has_storage_account"] = (
            StorageAccount.objects.filter(user=user, active=True).exists()
        )

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

        # Reject before any disk/DB write if the user can't cover the charge.
        from toto.vault.billing import preflight_upload_check
        can_pay, pay_error = preflight_upload_check(
            request.user, gateway.bucket, uploaded_file.size
        )
        if not can_pay:
            return JsonResponse({"error": pay_error}, status=402)

        directory = gateway.directory  # set at model level by admin

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

        # Collect any billing failures recorded by the post_save signal so we
        # can surface them to the user — they don't block the upload but should
        # be visible (e.g. "No matching tariff items found").
        billing_warnings = []
        try:
            from toto.tariffs.models import UsageRecord, UsageStatus
            failed_records = UsageRecord.objects.filter(
                source_type="vault_file",
                source_id=str(vault_file.pk),
                status=UsageStatus.FAILED,
            ).values_list("error_message", flat=True)
            billing_warnings = [msg for msg in failed_records if msg]
        except Exception:
            pass

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
            },
            "billing_warnings": billing_warnings,
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

        # ── Current user's storage account & usage ──────────
        user = self.request.user
        context["user_storage_account"] = None
        context["user_holdings"] = []
        context["user_recent_billing"] = []
        try:
            sa = StorageAccount.objects.select_related("ledger_account").get(user=user)
            context["user_storage_account"] = sa
            from toto.assets.models import AssetHolding
            context["user_holdings"] = list(
                AssetHolding.objects.filter(account=sa.ledger_account)
                .select_related("asset")
                .order_by("-balance_base_units")
            )
            from toto.tariffs.models import UsageRecord
            context["user_recent_billing"] = list(
                UsageRecord.objects.filter(
                    payer_account=sa.ledger_account,
                    source_type="vault_file",
                ).select_related("tariff").order_by("-created_at")[:10]
            )
        except StorageAccount.DoesNotExist:
            pass

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

        # ── Top-level counters ──────────────────────────────
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

        # ── Chart data ──────────────────────────────────────
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

        # ── Directory breakdown ─────────────────────────────
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

        # ── Storage totals & per-user quota ────────────────
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

        # ── Recent files ────────────────────────────────────
        context["recent_files"] = VaultFile.objects.filter(bucket=bucket).select_related(
            "owner", "directory"
        ).order_by("-uploaded_at")[:8]

        # ── Billing history ─────────────────────────────────
        context["bucket_tariff"] = bucket.tariff
        try:
            from toto.tariffs.models import UsageRecord
            context["billing_records"] = list(
                UsageRecord.objects
                .filter(metadata__bucket_id=bucket.pk)
                .select_related("payer_account", "tariff")
                .order_by("-created_at")[:15]
            )
        except Exception:
            context["billing_records"] = []

        return PageProcessor().decorate(context, self.request)


# ============================================================
# StorageAccount connect / disconnect
# ============================================================

class StorageAccountView(LoginRequiredMixin, TemplateView):
    """
    Lets the user connect or disconnect their LedgerAccount for vault billing.
    Shows available LedgerAccounts (type=USER) to link.
    """
    template_name = "vault/storage_account.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        try:
            storage_account = StorageAccount.objects.select_related(
                "ledger_account", "authorization"
            ).get(user=user)
        except StorageAccount.DoesNotExist:
            storage_account = None

        from toto.assets.models import AccountType, AssetHolding, LedgerAccount, WalletAuthorization
        available = list(LedgerAccount.objects.filter(
            user=user, account_type=AccountType.USER, active=True
        ).exclude(
            storage_accounts__user=user
        ))

        holdings = []
        available_authorizations = []
        if storage_account:
            holdings = list(
                AssetHolding.objects.filter(account=storage_account.ledger_account)
                .select_related("asset")
                .order_by("-balance_base_units")
            )
            available_authorizations = list(
                WalletAuthorization.objects.filter(
                    ledger_account=storage_account.ledger_account, active=True
                )
            )

        context["storage_account"] = storage_account
        context["available_accounts"] = available
        context["holdings"] = holdings
        context["available_authorizations"] = available_authorizations
        return PageProcessor().decorate(context, self.request)

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action")

        if action == "connect":
            ledger_pk = request.POST.get("ledger_account")
            name = request.POST.get("name", "").strip()
            if not ledger_pk:
                messages.error(request, "Please select a ledger account.")
                return redirect("vault:storage_account")

            from toto.assets.models import AccountType, LedgerAccount
            try:
                account = LedgerAccount.objects.get(
                    pk=ledger_pk, user=request.user, account_type=AccountType.USER, active=True
                )
            except LedgerAccount.DoesNotExist:
                messages.error(request, "Account not found or not available.")
                return redirect("vault:storage_account")

            StorageAccount.objects.update_or_create(
                user=request.user,
                defaults={"ledger_account": account, "name": name, "active": True},
            )
            messages.success(request, f"Storage account connected: {account.code}")

        elif action == "disconnect":
            StorageAccount.objects.filter(user=request.user).update(active=False)
            messages.success(request, "Storage account disconnected.")

        elif action == "reconnect":
            StorageAccount.objects.filter(user=request.user).update(active=True)
            messages.success(request, "Storage account reconnected.")

        elif action == "grant_authorization":
            try:
                sa = StorageAccount.objects.get(user=request.user, active=True)
            except StorageAccount.DoesNotExist:
                messages.error(request, "Connect a storage account first.")
                return redirect("vault:storage_account")

            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives.serialization import (
                Encoding, PublicFormat, PrivateFormat, NoEncryption,
            )
            from toto.assets.models import WalletAuthorization

            private_key = Ed25519PrivateKey.generate()
            public_pem = private_key.public_key().public_bytes(
                Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
            ).decode()
            private_pem = private_key.private_bytes(
                Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
            ).decode()

            auth = WalletAuthorization(
                name=f"Vault auto-auth ({sa.ledger_account.code})",
                ledger_account=sa.ledger_account,
                public_key=public_pem,
                active=True,
            )
            auth.set_private_key(private_pem)
            auth.save()

            sa.authorization = auth
            sa.save(update_fields=["authorization", "updated_at"])
            messages.success(request, "Authorization granted — the app can now sign transactions automatically.")

        elif action == "set_authorization":
            auth_pk = request.POST.get("authorization_pk")
            try:
                sa = StorageAccount.objects.get(user=request.user)
            except StorageAccount.DoesNotExist:
                messages.error(request, "Connect a storage account first.")
                return redirect("vault:storage_account")
            if auth_pk:
                from toto.assets.models import WalletAuthorization
                try:
                    auth = WalletAuthorization.objects.get(
                        pk=auth_pk, ledger_account=sa.ledger_account, active=True
                    )
                    sa.authorization = auth
                    sa.save(update_fields=["authorization", "updated_at"])
                    messages.success(request, f"Authorization set: {auth.name}")
                except WalletAuthorization.DoesNotExist:
                    messages.error(request, "Authorization not found.")
            else:
                sa.authorization = None
                sa.save(update_fields=["authorization", "updated_at"])
                messages.success(request, "Authorization removed. Manual confirmation will be required.")

        return redirect("vault:storage_account")


class BuyStorageTokensView(LoginRequiredMixin, View):
    """
    Let a user purchase storage tokens from the asset's reserve account.
    The purchase is free (admin pre-authorises by configuring the reserve).
    The user must have a StorageAccount to receive tokens.
    """

    def _get_storage_asset(self):
        from toto.tariffs.models import TariffItem
        from toto.vault.billing import STORAGE_REQUEST_METRIC_CODE
        item = (
            TariffItem.objects
            .filter(active=True, metric__code=STORAGE_REQUEST_METRIC_CODE)
            .select_related("charged_asset__reserve_account")
            .first()
        )
        if item and item.charged_asset and item.charged_asset.reserve_account_id:
            return item.charged_asset
        return None

    def _get_bucket_billing(self):
        from toto.vault.models import BucketBilling
        return (
            BucketBilling.objects
            .prefetch_related("token_prices__currency", "token_prices__revenue_account")
            .filter(token_prices__isnull=False)
            .first()
        )

    def get(self, request):
        import json
        from toto.vault.billing import get_payer_account
        from toto.assets.queries import get_asset_balance_display
        from toto.ui import PageProcessor

        asset = self._get_storage_asset()
        payer = get_payer_account(request.user)
        bucket_billing = self._get_bucket_billing()

        token_balance = None
        if asset and payer:
            token_balance = get_asset_balance_display(asset, payer)

        prices = []
        if bucket_billing and payer:
            for tp in bucket_billing.token_prices.select_related("currency"):
                payer_balance = get_asset_balance_display(tp.currency, payer)
                prices.append({
                    "pk": tp.pk,
                    "currency_pk": tp.currency.pk,
                    "unit_name": tp.currency.unit_name,
                    "currency_name": tp.currency.name,
                    "price_per_token": str(tp.price_per_token),
                    "payer_balance": str(payer_balance),
                    "decimals": tp.currency.decimals,
                })

        context = {
            "asset": asset,
            "payer_account": payer,
            "token_balance": token_balance,
            "bucket_billing": bucket_billing,
            "prices": prices,
            "prices_json": json.dumps(prices),
        }
        return render(request, "vault/buy_tokens.html", PageProcessor().decorate(context, request))

    def post(self, request):
        import uuid as _uuid
        from decimal import Decimal, InvalidOperation
        from toto.vault.billing import get_payer_account, _make_wallet_auth_signer
        from toto.vault.purchase import purchase_storage_tokens
        from toto.vault.models import StorageTokenPrice
        from toto.assets.services.assets import distribute_asset
        from django.core.exceptions import ValidationError

        asset = self._get_storage_asset()
        if not asset:
            messages.error(request, "No storage token is available for purchase right now.")
            return redirect("vault:buy_tokens")

        payer = get_payer_account(request.user)
        if not payer:
            messages.error(request, "Connect a ledger account in your storage account settings first.")
            return redirect("vault:storage_account")

        try:
            token_amount = Decimal(request.POST.get("token_amount", "0"))
            price_pk = request.POST.get("price_pk")
            signer = _make_wallet_auth_signer(request.user)

            if price_pk:
                tp = StorageTokenPrice.objects.select_related(
                    "currency", "revenue_account"
                ).get(pk=price_pk)
                ref = f"buy-tokens-{request.user.pk}-{_uuid.uuid4().hex[:8]}"
                result = purchase_storage_tokens(
                    payer_account=payer,
                    storage_asset=asset,
                    payment_asset=tp.currency,
                    token_amount=token_amount,
                    price_per_token=tp.price_per_token,
                    revenue_account=tp.revenue_account,
                    reference=ref,
                    username=request.user.username,
                    signer=signer,
                )
                messages.success(
                    request,
                    f"Purchased {result['token_amount']} {asset.unit_name} "
                    f"for {result['payment_amount']} {tp.currency.unit_name}."
                )
            else:
                raise ValidationError("No pricing is configured for storage tokens. Contact an administrator.")
        except (ValidationError, InvalidOperation, Exception) as exc:
            messages.error(request, str(exc))
        return redirect("vault:buy_tokens")
