"""The operator-facing half of the vault: Remote storage, and the Archive tab.

Split out of ``views.py`` (already ~2100 lines) the way ``peer_views`` and
``version_views`` were, and for the same reason.

## Two gates, and they refuse differently

``_operator_only`` guards the SLUGLESS index pages, where a 403 tells the caller
nothing they did not already know, and a sentence beats a mystery.

``_operator_or_404`` guards the SLUG-ADDRESSED pages. Those must answer 404,
because a 403 on a guessable slug confirms the bucket exists — the enumeration
oracle ``views.py`` documents twice and ``tests_access`` pins.
"""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.shortcuts import render
from django.views.generic import TemplateView

from toto.ui import PageProcessor

from . import access, remote_status
from .filetree import build_file_tree
from .models import Bucket, StorageBackend, VaultFile, external_buckets_allowed


def is_operator(user) -> bool:
    """``is_superuser`` does not imply ``is_staff`` in Django. Both count."""
    return bool(getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))


def _operator_only(request):
    if not is_operator(request.user):
        raise PermissionDenied("Remote storage is an operator surface.")


def _operator_or_404(request):
    if not is_operator(request.user):
        raise Http404("No such bucket.")


def _external_or_404():
    """A host that forbids external buckets should not render a page about them."""
    if not external_buckets_allowed():
        raise Http404("External buckets are disabled on this host.")


def vault_render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


class VaultPageMixin:
    """Put the platform skin into a class-based view's context.

    ``oya/base.html`` renders the site header, the logo, the theme colours and
    the light/dark toggle from what ``PageProcessor.decorate`` injects —
    ``platform``, ``theme``, ``font``, ``logo``, ``header_nav_items``. A
    function view gets that by rendering through ``vault_render``; a
    ``TemplateView`` does not, because ``get_context_data`` is the only hook
    and nothing decorates it.

    Without this the page still renders, which is exactly why it is easy to
    miss: the content is all there, unstyled and with no header, and it looks
    like a CSS problem rather than a missing context.
    """

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return PageProcessor().decorate(context, self.request)


class RemoteBucketsView(VaultPageMixin, LoginRequiredMixin, TemplateView):
    """Every bucket whose bytes live somewhere else, and how it is doing.

    Reads stamped columns only — no probe, no network, per the rule the whole
    remote surface rests on. "Never checked" is a first-class answer.
    """

    template_name = "vault/remote_buckets.html"

    def get(self, request, *args, **kwargs):
        _external_or_404()
        _operator_only(request)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        remote = (
            Bucket.objects
            .exclude(storage_backend__in=["", StorageBackend.LOCAL])
            .select_related("peer", "provider", "owner")
            .annotate(file_count=Count("files"),
                      total_bytes=Sum("files__file_size_bytes"))
            .order_by("name")
        )
        rows = []
        for bucket in remote:
            info = remote_status.peer_info(bucket)
            rows.append({
                "bucket": bucket,
                "info": info,
                "reachability": (info or {}).get("reachability", "unknown"),
                # The endpoint is assembled HERE, never parsed in a template.
                "endpoint": (bucket.storage_config or {}).get("endpoint_url", ""),
                "region": (bucket.storage_config or {}).get("region_name", ""),
                "prefix": (bucket.storage_config or {}).get("prefix", ""),
                "remote_bucket_name": (bucket.storage_config or {}).get("bucket_name", ""),
            })
        context.update({
            "active_tab": "remote",
            "rows": rows,
            "s3_rows": [r for r in rows if r["bucket"].storage_backend == StorageBackend.S3],
            "mount_rows": [r for r in rows
                           if r["bucket"].storage_backend == StorageBackend.REMOTE_TOTO],
            "local_count": Bucket.objects.filter(
                Q(storage_backend="") | Q(storage_backend=StorageBackend.LOCAL)).count(),
        })
        return context


class ArchiveView(VaultPageMixin, LoginRequiredMixin, TemplateView):
    """The Archive tab: pick ONE bucket, then bundle its files into a .zip.

    Bucket-first is deliberate, and matches the endpoint exactly:
    ``CreateZipView`` requires a ``source_directory_id``, validates every
    selected file against that directory's bucket, and is gated on the BUCKET
    OWNER — so a cross-bucket selection could never be accepted, and offering
    one would be a dead control. Only buckets this user owns (and, per the
    on-device rule, only buckets whose bytes live on this host) are offered.

    The tree is built from exactly the queryset the endpoint accepts — same
    bucket, not encrypted, local content — per ``build_file_tree``'s own rule:
    rows carrying an ACTION use the endpoint's queryset, or the page grows
    buttons that cannot work.
    """

    template_name = "vault/archive.html"

    def _archivable_buckets(self, user):
        """Buckets this user may archive from: OWNED and ON-DEVICE.

        Owned because CreateZipView refuses anyone but the bucket owner (or a
        superuser). On-device because the zip task opens local handles — a
        remote bucket's mirror stubs have none, and offering the bucket at all
        would offer an empty tree.
        """
        owned = Bucket.objects.filter(
            Q(storage_backend="") | Q(storage_backend=StorageBackend.LOCAL))
        if not user.is_superuser:
            owned = owned.filter(owner=user)
        return owned.order_by("name")

    def _zippable(self, user, bucket):
        """What CreateZipView will actually accept for this bucket."""
        from .filetree import accessible_files

        return (accessible_files(user)
                .filter(bucket=bucket, is_encrypted=False)
                .filter(access.local_content_q()))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        buckets = self._archivable_buckets(user)
        selected = None
        slug = self.request.GET.get("bucket", "")
        if slug:
            selected = buckets.filter(slug=slug).first()

        context.update({
            "active_tab": "archive",
            "buckets": buckets,
            "selected_bucket": selected,
            "zip_enabled": self.zip_enabled(),
        })
        if selected is not None:
            from .models import VaultDirectory

            context.update({
                "tree": build_file_tree(
                    user, queryset=self._zippable(user, selected)),
                # The endpoint needs a source directory; the archive lands
                # beside it. A bucket with no directories cannot zip, and the
                # page says so instead of offering a dead button.
                "directories": VaultDirectory.objects.filter(
                    bucket=selected).order_by("name"),
                "archives": (
                    VaultFile.objects.filter(
                        bucket=selected, file_type="zip")
                    .select_related("directory")
                    .order_by("-uploaded_at")[:25]
                ),
            })
        else:
            context["archives"] = (
                VaultFile.objects.filter(
                    bucket__in=buckets, file_type="zip")
                .select_related("bucket", "directory")
                .order_by("-uploaded_at")[:25]
            )
        return context

    @staticmethod
    def zip_enabled() -> bool:
        from django.apps import apps as django_apps

        return django_apps.is_installed("toto.workflows")


class RemoteS3CreateView(VaultPageMixin, LoginRequiredMixin, TemplateView):
    """Create an S3-compatible bucket. Staff only, like the whole Remote tab."""

    template_name = "vault/remote_s3_new.html"

    def get(self, request, *args, **kwargs):
        _external_or_404()
        _operator_only(request)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        from .forms import S3BucketForm

        context = super().get_context_data(**kwargs)
        context.setdefault("form", S3BucketForm())
        context["active_tab"] = "remote"
        return context

    def post(self, request, *args, **kwargs):
        from django.contrib import messages
        from django.shortcuts import redirect
        from django.utils.text import slugify

        from .forms import S3BucketForm
        from .models import StorageBackend

        _external_or_404()
        _operator_only(request)
        form = S3BucketForm(request.POST)
        if not form.is_valid():
            context = self.get_context_data(form=form)
            response = vault_render(request, self.template_name, context)
            response["Cache-Control"] = "no-store"
            return response

        bucket = Bucket(
            name=form.cleaned_data["name"],
            slug=slugify(form.cleaned_data["name"]),
            owner=request.user,
            storage_backend=StorageBackend.S3,
            provider=form.cleaned_data.get("provider"),
            storage_config=form.storage_config(),
        )
        bucket.full_clean(exclude=["slug"])
        bucket.save()
        messages.success(request, "S3 bucket configured. Credentials come from "
                                  "the environment until a sealed credential "
                                  "is set.")
        return redirect("vault:remote_buckets")


class RemoteMountCreateView(VaultPageMixin, LoginRequiredMixin, TemplateView):
    """Mount a bucket from a federated platform. Staff only, federated only.

    The pairing probe runs inline, the admin's precedent: one bounded call at
    pairing time, so a mangled code or an expired grant surfaces NOW instead of
    during the first refresh. A failed probe does not undo the pairing — the
    row keeps the error and the page says so.
    """

    template_name = "vault/remote_mount_new.html"

    def get(self, request, *args, **kwargs):
        _external_or_404()
        _operator_only(request)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        from .forms import FederatedMountForm

        context = super().get_context_data(**kwargs)
        context.setdefault("form", FederatedMountForm())
        context["active_tab"] = "remote"
        context["has_federated_hosts"] = context["form"].has_federated_hosts
        return context

    def post(self, request, *args, **kwargs):
        from django.contrib import messages
        from django.db import transaction
        from django.shortcuts import redirect
        from django.utils.text import slugify

        from .forms import FederatedMountForm
        from .models import StorageBackend
        from .peering import BucketPeer, apply_manifest

        _external_or_404()
        _operator_only(request)
        form = FederatedMountForm(request.POST)
        if not form.is_valid():
            context = self.get_context_data(form=form)
            response = vault_render(request, self.template_name, context)
            response["Cache-Control"] = "no-store"
            return response

        code = form.cleaned_data["decoded_code"]
        with transaction.atomic():
            peer = BucketPeer(
                label=form.cleaned_data["name"],
                base_url=form.cleaned_data["paired_host"].rstrip("/"),
                grant_uid=code["grant_uid"],
                magic_token=code["magic_token"],
                remote_bucket_slug=code.get("bucket", ""),
                capabilities=code.get("rights", []),
                paired_by=request.user,
            )
            peer.set_api_key(code["api_key"])
            peer.save()
            bucket = Bucket(
                name=form.cleaned_data["name"],
                slug=slugify(form.cleaned_data["name"]),
                owner=request.user,
                storage_backend=StorageBackend.REMOTE_TOTO,
                peer=peer,
            )
            bucket.full_clean(exclude=["slug"])
            bucket.save()

        # Probe AFTER the commit: a network failure must not roll the pairing
        # back, it must be stamped on it.
        try:
            from .peer_client import PeerClient

            manifest = PeerClient(peer).manifest()
        except Exception as exc:  # noqa: BLE001 — every transport failure, one stamp
            BucketPeer.objects.filter(pk=peer.pk).update(probe_error=str(exc))
            messages.warning(
                request,
                f"Mounted, but the probe failed: {exc}. Fix the code or the "
                "grant on the exporting host, then re-pair.")
        else:
            apply_manifest(peer, manifest)
            messages.success(
                request,
                f"Mounted — {peer.base_url} answered for bucket "
                f"'{peer.remote_bucket_slug}'.")
        return redirect("vault:remote_buckets")


class RemoteBucketTestView(LoginRequiredMixin, TemplateView):
    """The online connection test: one bounded call, on an operator's click.

    POST-only, staff-only, slug-addressed — so a refusal is a 404 (the
    enumeration-oracle rule), and no page render ever triggers it. For a mount
    it is the pairing probe re-run (and STAMPED, so the listing's health column
    learns from it); for S3 it is a bounded head_bucket, which this backend
    never had before — the client is lazy and otherwise fails on first read.
    """

    http_method_names = ["post"]

    def post(self, request, slug, *args, **kwargs):
        from django.http import JsonResponse

        from .models import StorageBackend
        from .peering import apply_manifest

        _external_or_404()
        _operator_or_404(request)
        bucket = (Bucket.objects.select_related("peer")
                  .exclude(storage_backend__in=["", StorageBackend.LOCAL])
                  .filter(slug=slug).first())
        if bucket is None:
            raise Http404("No such bucket.")

        if bucket.storage_backend == StorageBackend.REMOTE_TOTO:
            peer = bucket.peer
            if peer is None:
                return JsonResponse({"ok": False, "detail": "No pairing."})
            try:
                from .peer_client import PeerClient

                manifest = PeerClient(peer).manifest()
            except Exception as exc:  # noqa: BLE001 — one sentence, stamped
                type(peer).objects.filter(pk=peer.pk).update(
                    probe_error=str(exc), last_error=str(exc))
                response = JsonResponse({"ok": False, "detail": str(exc)})
            else:
                apply_manifest(peer, manifest)
                response = JsonResponse({
                    "ok": True,
                    "detail": f"{peer.base_url} answered for bucket "
                              f"'{peer.remote_bucket_slug}'."})
        else:
            from .storage_backends import get_bucket_storage

            try:
                ok, detail = get_bucket_storage(bucket).probe()
            except Exception as exc:  # noqa: BLE001 — outbound guard refusals land here
                ok, detail = False, str(exc)
            response = JsonResponse({"ok": ok, "detail": detail})

        response["Cache-Control"] = "no-store"
        return response
