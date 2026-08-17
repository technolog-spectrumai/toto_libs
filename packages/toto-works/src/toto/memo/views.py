"""
File-based presentation viewer + browser editor.

A presentation is a single self-contained ``.pxml`` vault file
(``file_type="pxml"``) parsed by :mod:`toto.memo.presentation_format`. These
views never touch the database for presentation content — the vault file is the
single source of truth.

A deck is identified by its TYPE and nothing else. It used to be identified by
reading it: decks were typed ``presentation`` but named ``.xml``, so every
listing sniffed up to 300 files off disk and retyped rows behind the user's
back. ``.pxml`` and vault migration 0021 replaced that with a name. The legacy
spelling ``presentation`` is still read, for the rows 0021 could not reach
(mirrored stubs, s3/remote buckets, encrypted decks).
"""

from __future__ import annotations

import hashlib
import json
import mimetypes

from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core.files.base import ContentFile
from django.contrib import messages
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views import View
from django.conf import settings
from django.views.decorators.http import require_POST
from django.contrib.auth.mixins import LoginRequiredMixin

from toto.editor.views import BaseFileDisplayView
from toto.core import assistant
from toto.ui import PageProcessor
from toto.vault import access, locks, versions
from toto.vault.filetree import accessible_files
from toto.quota import QuotaExceeded, check_quota, record_usage
from toto.quota.charge import InsufficientFunds, charge, check_funds, price_for
from toto.memo.models import MemoQuotaPolicy, MemoUsageEvent
from toto.vault.models import VaultFile
from toto.vault.views import (
    _unique_file_key,
    new_file_picker_json,
    resolve_new_file_target,
)

from . import presentation_format, render_pdf
from toto.antivirus.sanitize import sanitize_svg as clean_svg_markup
from .media import image_bytes_to_data_uri

# Vault file types that can be embedded into a slide body.
_MEDIA_TYPES = ["image", "svg"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_raw(vault_file: VaultFile) -> str:
    """Raw UTF-8 text of the vault file via a fresh storage handle."""
    with vault_file.file.storage.open(vault_file.file.name, "rb") as fh:
        return fh.read().decode("utf-8")


#: Both spellings of the deck class. 0021 renamed it; the legacy string stays
#: readable because that migration cannot reach mirrored, remote or encrypted
#: rows, and a deck it missed should be dull rather than missing.
DECK_TYPES = ["pxml", "presentation"]


def _get_owned_file(request, file_pk) -> VaultFile:
    """Fetch a deck owned by the user.

    The type is the whole check now. Reading the bytes to confirm the file
    really is a deck bought nothing here — this view is about to parse it
    anyway, and a mistyped file renders as an empty deck rather than a 404.
    """
    return get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory", "owner")
        .filter(access.local_content_q()),
        pk=file_pk,
        owner=request.user,
        file_type__in=DECK_TYPES,
    )


def _read_presentation(vault_file: VaultFile) -> presentation_format.Presentation:
    try:
        raw = vault_file.file.read().decode("utf-8")
    except Exception:
        raw = ""
    try:
        return presentation_format.loads(raw)
    except presentation_format.PresentationParseError:
        # Corrupt / non-presentation content — start blank rather than blowing
        # up the editor.  Saving overwrites with valid XML.
        return presentation_format.new_presentation(title=vault_file.title)


# ---------------------------------------------------------------------------
# Viewer
# ---------------------------------------------------------------------------

class PresentationView(View):
    """Render a presentation vault file as a reveal.js slideshow."""

    template_name = "memo/present.html"

    def get(self, request, file_pk):
        vault_file = get_object_or_404(
            VaultFile.objects.select_related("bucket", "directory", "owner")
        .filter(access.local_content_q()),
            pk=file_pk,
            file_type__in=DECK_TYPES,
        )

        if vault_file.is_encrypted:
            return HttpResponseForbidden("Cannot display an encrypted file.")

        # Visibility check mirrors toto.vod.views.vault_file_play.
        if not vault_file.is_public:
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            can_access = vault_file.owner == request.user
            if not can_access and vault_file.directory:
                can_access = vault_file.directory.user_can_access(request.user)
            if not can_access:
                return HttpResponseForbidden()

        presentation = _read_presentation(vault_file)

        # Read-only since 8/2026: decks are authored in the desktop app, so
        # the player links back to the READ page, never to an editor.
        context = PageProcessor().decorate(
            {
                "vault_file": vault_file,
                "presentation": presentation,
                "read_url": reverse("memo:read", args=[vault_file.pk]),
            },
            request,
        )
        return render(request, self.template_name, context)


# ---------------------------------------------------------------------------
# Editor
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@login_required
def presentation_export_pdf(request, file_pk):
    """One page per slide, rendered from the same slide.css as everything else."""
    vault_file = _get_owned_file(request, file_pk)
    presentation = _read_presentation(vault_file)

    tariff = price_for(request.user, "memo")
    try:
        check_quota(MemoQuotaPolicy, "memo.pdf", 1, request.user)
        check_funds(request.user, tariff, "memo.pdf", 1)
    except (QuotaExceeded, InsufficientFunds) as exc:
        # Plain text, not messages+redirect: this is a download target, and a
        # redirect would replace the page the user is looking at.
        return HttpResponse(str(exc), status=exc.status_code, content_type="text/plain")

    try:
        raw = render_pdf.render(presentation)
    except render_pdf.PdfUnavailable as exc:
        # A deployment fact, not something the user can fix by trying again.
        return HttpResponse(str(exc), status=503, content_type="text/plain")

    # Charged after the render succeeds. A synchronous call that returns bytes
    # has no paid-for-but-undelivered window, so there is nothing to refund.
    _src = {"source_type": "vault.VaultFile", "source_id": str(vault_file.pk),
            "source_label": vault_file.title or ""}
    record_usage(MemoUsageEvent, "memo.pdf", 1, request.user, **_src)
    charge(request.user, tariff, "memo.pdf", 1, **_src)

    base = (vault_file.title or "presentation").rsplit(".", 1)[0]
    response = HttpResponse(raw, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{slugify(base) or "deck"}.pdf"'
    return response


# ---------------------------------------------------------------------------
# Index / gallery
# ---------------------------------------------------------------------------

class PresentationIndexView(View):
    """The gallery: every deck the current user can open, with a thumbnail."""

    template_name = "memo/index.html"
    PER_PAGE = 12

    def get(self, request):
        from django.core.paginator import Paginator
        from django.db.models import Q

        # One query, no file reads. This listing used to open up to 300 files
        # off disk on every visit — including anonymous ones — to find out
        # which generic .xml rows were decks, and rewrote their file_type as a
        # side effect of rendering a page. Decks say what they are now.
        qs = VaultFile.objects.filter(
            file_type__in=DECK_TYPES, is_encrypted=False
        ).filter(access.local_content_q()
        ).select_related("owner", "bucket", "directory")
        if request.user.is_authenticated:
            qs = qs.filter(Q(is_public=True) | Q(owner=request.user))
        else:
            qs = qs.filter(is_public=True)
        qs = qs.order_by("-uploaded_at", "title")

        page = Paginator(qs, self.PER_PAGE).get_page(request.GET.get("page"))

        presentations = []
        for f in page.object_list:
            presentations.append({
                "file_pk": f.pk,
                "title": f.title,
                "owner": f.owner.username,
                "uploaded": f.uploaded_at,
                "location": _location_of(f),
                "is_owner": request.user.is_authenticated and f.owner_id == request.user.id,
                "present_url": reverse("memo:present", args=[f.pk]),
                "read_url": reverse("memo:read", args=[f.pk]),
                # Only the current page is parsed — which is the point of
                # paginating at all. A gallery of 300 decks used to read and
                # fully parse all 300 files on every visit.
                **_cover(f),
            })

        buckets_json, directories_json = new_file_picker_json(request.user)
        context = PageProcessor().decorate(
            {
                "presentations": presentations,
                "page_obj": page,
                "is_paginated": page.has_other_pages(),
                "buckets_json": buckets_json,
                "directories_json": directories_json,
            },
            request,
        )
        return render(request, self.template_name, context)


def _location_of(vault_file) -> str:
    location = vault_file.bucket.name if vault_file.bucket else "\u2014"
    if vault_file.directory:
        location = f"{location} / {vault_file.directory.full_path()}"
    return location


def _cover(vault_file) -> dict:
    """Slide one and the deck theme, for the card thumbnail.

    The thumbnail is a real slide rendered through the same `_slide.html` and
    `slide.css` as the player, just scaled down — so a slide that is too full
    looks too full on the card. A screenshot or a text summary would be one more
    thing to keep in step.
    """
    try:
        presentation = presentation_format.loads(_read_raw(vault_file))
    except Exception:                                  # noqa: BLE001
        return {"cover": None, "theme": "black", "font": "sans", "slide_count": 0}
    return {
        "cover": presentation.slides[0] if presentation.slides else None,
        "theme": presentation.theme,
        "font": presentation.font,
        "slide_count": len(presentation.slides),
    }


class PresentationReadView(LoginRequiredMixin, View):
    """A deck as a page.

    The player is for standing up in front of people; this is for reading one
    at a desk, and for a link that lands somewhere quotable. Same slides, same
    stylesheet, stacked instead of paged.

    Read-only like everything else left here — it renders what the file says
    and offers nothing that could change it.

    LoginRequiredMixin is load-bearing, not decoration: `_get_owned_file`
    filters on `owner=request.user`, and handing an AnonymousUser to a foreign
    key comparison raises TypeError — so without the mixin an anonymous visitor
    got a 500 where they should have got the login page.
    """

    def get(self, request, file_pk):
        vault_file = _get_owned_file(request, file_pk)
        presentation = _read_presentation(vault_file)
        # Decorated like every other page that extends oya/base.html. Without
        # it there is no `theme`, and base.html writes the Tailwind config as
        # `colors: {{ theme.theme.colors }}` — which with no theme becomes
        # `colors: }`, a JS syntax error that takes the whole config down with
        # it, so the page loses its palette and dark mode stops working.
        return render(request, "memo/read.html", PageProcessor().decorate({
            "vault_file": vault_file,
            "presentation": presentation,
            "slides": presentation.slides,
        }, request))
