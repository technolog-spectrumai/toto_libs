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


class RemoteBucketsView(LoginRequiredMixin, TemplateView):
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


class ArchiveView(LoginRequiredMixin, TemplateView):
    """The same tree as Files, carrying the zip actions Files does not.

    The tree is built from EXACTLY the queryset ``CreateZipView`` accepts —
    same bucket rules, not encrypted, local content only. ``build_file_tree``'s
    own docstring is the reason: *"A page whose rows carry an ACTION should pass
    the same queryset its endpoint accepts — otherwise it lists rows whose
    button cannot work, and the failure surfaces as a dead control rather than
    as anything a reader could diagnose."*
    """

    template_name = "vault/archive.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context.update({
            "active_tab": "archive",
            "tree": build_file_tree(user, queryset=self.zippable_queryset(user)),
            "zip_enabled": self.zip_enabled(),
            "archives": (
                VaultFile.objects.filter(owner=user, file_type="zip")
                .select_related("bucket", "directory")
                .order_by("-uploaded_at")[:25]
            ),
        })
        return context

    @staticmethod
    def zippable_queryset(user):
        """What CreateZipView will actually accept, and nothing else."""
        from .filetree import accessible_files

        return (accessible_files(user)
                .filter(is_encrypted=False)
                .filter(access.local_content_q()))

    @staticmethod
    def zip_enabled() -> bool:
        from django.apps import apps as django_apps

        return django_apps.is_installed("toto.workflows")
