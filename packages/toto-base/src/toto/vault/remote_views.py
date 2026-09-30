"""The Archive tab, and the page skin every class-based vault view shares.

Split out of ``views.py`` (already ~2100 lines) the way ``peer_views`` and
``version_views`` were, and for the same reason. It used to carry the Remote
tab too — its listing, its two create pages and its connection test; those
folded into Storage → Management (``manage_views``, 2026-09-30), where every
bucket is listed with its health and made, tested and deleted.
"""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.views.generic import TemplateView

from toto.ui import PageProcessor

from . import access
from .filetree import build_file_tree
from .models import Bucket, StorageBackend, VaultFile


class VaultPageMixin:
    """Put the platform skin into a class-based view's context.

    ``oya/base.html`` renders the site header, the logo, the theme colours and
    the light/dark toggle from what ``PageProcessor.decorate`` injects —
    ``platform``, ``theme``, ``font``, ``logo``, ``header_nav_items``. A
    function view gets that by rendering through ``PageProcessor().decorate``;
    a ``TemplateView`` does not, because ``get_context_data`` is the only hook
    and nothing decorates it.

    Without this the page still renders, which is exactly why it is easy to
    miss: the content is all there, unstyled and with no header, and it looks
    like a CSS problem rather than a missing context.
    """

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return PageProcessor().decorate(context, self.request)


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
                    access.gate_by_bucket(user, VaultFile.objects.filter(
                        bucket=selected, file_type="zip"))
                    .select_related("directory")
                    .order_by("-uploaded_at")[:25]
                ),
            })
        else:
            context["archives"] = (
                access.gate_by_bucket(user, VaultFile.objects.filter(
                    bucket__in=buckets, file_type="zip"))
                .select_related("bucket", "directory")
                .order_by("-uploaded_at")[:25]
            )
        return context

    @staticmethod
    def zip_enabled() -> bool:
        from django.apps import apps as django_apps

        return django_apps.is_installed("toto.workflows")
