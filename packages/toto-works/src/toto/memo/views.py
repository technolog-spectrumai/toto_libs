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

import logging

import hashlib
import json
import mimetypes

from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core.files.base import ContentFile
from django.contrib import messages
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse, reverse_lazy
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views import View
from django.conf import settings
from django.views.decorators.http import require_POST
from django.contrib.auth.mixins import LoginRequiredMixin

from toto.editor.views import BaseFileDisplayView
from toto.core import assistant
from toto.ui import PageProcessor
from toto.vault import access, editing, locks, versions
from toto.vault.filetree import accessible_files
from toto.vault.views import create_empty_vault_file, resolve_new_file_target
from toto.quota import QuotaExceeded, check_quota, record_usage
from toto.quota.charge import InsufficientFunds, charge, check_funds, price_for
from toto.memo.models import MemoQuotaPolicy, MemoUsageEvent
from toto.vault.models import VaultFile, file_edits_allowed
from toto.vault.views import (
    _unique_file_key,
    new_file_picker_json,
    resolve_new_file_target,
)

from . import presentation_format, render_pdf
from toto.antivirus.sanitize import sanitize_svg as clean_svg_markup
from .media import image_bytes_to_data_uri

log = logging.getLogger(__name__)

# Vault file types that can be embedded into a slide body.
_MEDIA_TYPES = ["image", "svg"]




def _maybe_reverse(name, *args):
    """A URL if the route is registered, else "".

    A host may mount only the reading half of this app, in which case the
    authoring routes are absent and `reverse` raises. Templates already treat
    "" as "do not offer the link", so the empty string is the right answer
    rather than an exception.
    """
    try:
        return reverse(name, args=args)
    except NoReverseMatch:
        return ""


def _tiptap_import_map():
    """cyprian's TipTap import map, imported at call time.

    Same wheel, so the import resolves wherever toto-works is installed; what
    it needs at RUNTIME is cyprian's static dir. Keeping the import inside the
    editor view is what lets a host mount the reader alone.
    """
    from toto.cyprian import tiptap

    return tiptap.import_map_json()


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

    Its bucket's clearances (2026-09-30) come first: a deck in a kept bucket
    whose owner holds none of them is missing to its owner too.
    """
    return get_object_or_404(
        access.gate_by_bucket(request.user, VaultFile.objects.select_related(
            "bucket", "directory", "owner").filter(access.local_content_q())),
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

        # The vault's one read rule, its bucket's clearances included
        # (2026-09-30): a deck in a kept bucket is its holders' alone, public
        # or not, its owner included. A visitor is sent
        # to log in; a member who may not read it gets the 404 a missing
        # deck gets — its existence is not theirs to learn here.
        if not access.may_read(request.user, vault_file):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            raise Http404("No such deck.")

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


def _first_unrenderable_slide(presentation):
    """The 1-based number of the first slide that will not lay out, or None.

    Only ever called AFTER a failure, so the cost is paid on a request that
    already lost. Renders each slide alone: the deck is the sum of its slides,
    so whatever breaks the whole breaks one of them — and if none of them fails
    alone the answer is None and the message simply omits it rather than
    guessing.
    """
    import copy

    from . import render_pdf

    slides = list(getattr(presentation, "slides", []) or [])
    for index, slide in enumerate(slides, start=1):
        one = copy.copy(presentation)
        one.slides = [slide]
        try:
            render_pdf.render(one)
        except Exception:       # noqa: BLE001 — this IS the diagnosis
            return index
    return None


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

    # In a Compute Gear where there is one, in this process where there is not.
    #
    # Still SYNCHRONOUS either way, which is the point: a deck export returns
    # bytes to a download, and turning it into a poll-and-wait would change a
    # working interaction for no benefit. A Gear render costs a container
    # start (~a second) on top of the render itself — worth it to keep the
    # heavy library out of this image, and cheap enough not to notice.
    try:
        lease = render_pdf.gear_for(request.user)
        if lease is None:
            raw = render_pdf.render(presentation)
        else:
            raw = render_pdf.render_in_gear(presentation, lease=lease,
                                            user=request.user)
    except render_pdf.PdfUnavailable as exc:
        # A deployment fact, not something the user can fix by trying again.
        return HttpResponse(str(exc), status=503, content_type="text/plain")
    except Exception as exc:  # noqa: BLE001 — a Gear that could not run it
        # NoGear and JobFailed both land here. Neither is a 500: the first is
        # something the user fixes on the Compute Gears page, the second is a
        # render that genuinely failed.
        #
        # A LAYOUT ERROR FROM WEASYPRINT reaches this too, and used to arrive
        # as its own words — "unsupported operand type(s) for *: 'NoneType'
        # and 'int'" — which tells the reader nothing about the deck and
        # nothing about what to do. Reported 2026-09-06 and never reproduced
        # here: a fresh deck exports, and so does every block type (image,
        # video, formula, list, text) including a broken data URI, a missing
        # file and an SVG with no intrinsic size. So the deck is not
        # reconstructible from the message, and the message is what has to
        # improve — with the exception logged, which is what would have
        # identified the deck the first time.
        log.exception("memo: PDF export failed for vault file %s (%s slides, "
                      "theme=%s, block types=%s)", vault_file.pk,
                      len(getattr(presentation, "slides", []) or []),
                      getattr(presentation, "theme", "?"),
                      sorted({b.type for sl in getattr(presentation, "slides", [])
                              for b in getattr(sl, "blocks", [])}))
        if isinstance(exc, TypeError):
            # WHICH SLIDE. The old message said "the details are in the server
            # log", which meant somebody had to go and read it — and the log
            # did not name the slide either, so a deck of forty was still a
            # search. Rendering slide-by-slide costs one extra pass on a path
            # that has ALREADY failed, and turns "somewhere in your deck" into
            # a number the author can go and look at.
            culprit = _first_unrenderable_slide(presentation)
            where = (f" Slide {culprit} is the first one that fails."
                     if culprit else "")
            return HttpResponse(
                "This deck could not be laid out for printing. That is a bug "
                "in the exporter rather than something wrong with your deck." +
                where + " Present and print from the browser meanwhile.",
                status=503, content_type="text/plain")
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
        # The vault's read rule: what this person may open — their own,
        # public decks, decks shared with them — less the decks in buckets
        # kept to clearances they hold none of (2026-09-30). A visitor sees
        # public decks in no kept bucket.
        qs = VaultFile.objects.filter(
            file_type__in=DECK_TYPES, is_encrypted=False
        ).filter(access.local_content_q()
        ).select_related("owner", "bucket", "directory")
        if request.user.is_authenticated:
            qs = qs.filter(pk__in=accessible_files(request.user, file_types=DECK_TYPES))
        else:
            qs = access.gate_by_bucket(request.user, qs, open=Q(is_public=True))
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
                # Asked once for the page: the form posts to a route the gate
                # would 402 anyway, and a button that answers "not in your plan"
                # is worse than no button.
                #
                # The route check comes FIRST and is not merely belt-and-braces:
                # on a host that mounts the reader alone, `memo:create` does not
                # exist, and the template reverses it inside this guard — so a
                # door that opened would 500 the gallery. `door_for` is not even
                # asked there, which is right: there is nothing to be entitled
                # to.
                "can_create": bool(
                    _maybe_reverse("memo:create")
                    and editing.door_for(
                        request.user, entitlement=ENTITLEMENT,
                        metric_code=SAVE_METRIC,
                        policy_model=MemoQuotaPolicy).open),
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
            # The way in to the editor. `_get_owned_file` already made this the
            # owner, so the link is never a 404 — whether they may SAVE is the
            # further question the door answers on that page, which is where a
            # subscriber without the plan is told what it costs.
            #
            # EMPTY where the route is not registered. A host can mount the
            # reader half of this app alone (zenobia does), and then
            # `memo:edit` does not exist — an unguarded reverse here would
            # 500 the READ page, which is the half such a host actually wants.
            "edit_url": _maybe_reverse("memo:edit", vault_file.pk),
        }, request))


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
#
# Restored 8/2026 under the shared regime in `toto.vault.editing`: one lock, one
# base-hash precondition, one version per save, one meter. Every route below is
# POST except the editor page, because `SubscriptionGateMiddleware` decides
# entitlement from `app_name` and lets safe methods through — a save answering
# GET would be a save with no paywall.

#: What a save counts against, and the plan that has to include it.
SAVE_METRIC = "memo.save"
ENTITLEMENT = "memo"

#: A deck carries its images inline as data URIs, so the whole file arrives in
#: one body. Read through `_read_json_body`, never `request.body`, which Django
#: caps at DATA_UPLOAD_MAX_MEMORY_SIZE (2.5 MB by default and raised by no host).
MAX_DECK_BYTES = 32 * 1024 * 1024


def _media_list(user) -> list[dict]:
    """The user's embeddable images and SVGs, for the editor's picker.

    Access-checked through `accessible_files`, and encrypted files are skipped:
    their bytes are ciphertext and embedding them would inline noise.
    """
    files = (
        accessible_files(user, file_types=_MEDIA_TYPES)
        .filter(is_encrypted=False)
        .order_by("bucket__name", "title")[:500]
    )
    items = []
    for f in files:
        bucket_name = f.bucket.name if f.bucket else "—"
        location = (f"{bucket_name} / {f.directory.full_path()}"
                    if f.directory_id else bucket_name)
        items.append({
            "pk": f.id,
            "title": f.title or f.key,
            "file_type": f.file_type,
            "location": location,
        })
    return items


def _read_json_body(request, limit: int):
    """The request body, without Django's 2.5 MB form ceiling.

    `request.read()` is not size-checked, so the limit becomes ours to state.
    Safe to call after the CSRF middleware: for `application/json` Django's
    `request.POST` never touches the stream.
    """
    raw = request.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("too-big")
    return json.loads(raw or b"{}")


class PresentationEditView(LoginRequiredMixin, View):
    """The deck editor. Owner only, and the one place the door is decided."""

    template_name = "memo/edit.html"
    login_url = reverse_lazy("core:login")

    def get(self, request, file_pk):
        vault_file = _get_owned_file(request, file_pk)
        if vault_file.is_encrypted:
            from toto.vault.access import encrypted_lock_response
            return encrypted_lock_response(request, vault_file)

        door = editing.door_for(
            request.user, vault_file, entitlement=ENTITLEMENT,
            metric_code=SAVE_METRIC, policy_model=MemoQuotaPolicy)
        if not door.open:
            return editing.closed_response(request, door)

        presentation = _read_presentation(vault_file)
        context = PageProcessor().decorate({
            "vault_file": vault_file,
            # Hydration payloads. The template emits these through
            # `json_script`, which JSON-encodes what it is given, so they must
            # be plain Python — handing it an already-serialised string produces
            # an island that parses back into a *string* with every property
            # undefined.
            "presentation_json": presentation.to_dict(),
            "vault_media_json": _media_list(request.user),
            "config_json": {
                "canEdit": door.writable,
                "filePk": vault_file.pk,
                # Sent back with every save; the endpoint answers 409 if the
                # file moved underneath this buffer.
                "contentHash": vault_file.content_hash or "",
                "urls": {
                    "save": reverse("memo:save", args=[file_pk]),
                    "embed": reverse("memo:media_embed"),
                    "upload": reverse("memo:media_upload"),
                    "index": reverse("memo:index"),
                },
                # Strings the editor puts in a browser prompt, where a
                # {% trans %} in the template cannot reach.
                "text": {"linkPrompt": _("Link address")},
            },
            # Imported HERE, not at module scope, and that placement is the
            # whole of what makes a read-only deployment possible.
            #
            # The vendored TipTap bundle lives in cyprian, and this is the only
            # line in the module that needs it — the EDIT view. A module-scope
            # import made every memo page, including the gallery and the
            # player, unimportable on a host without toto.cyprian; a host that
            # mounts only the reader now needs neither cyprian nor its static
            # dir. A host that mounts the editor still needs both, unchanged.
            "tiptap_import_map": _tiptap_import_map(),
            "read_url": reverse("memo:read", args=[file_pk]),
            "present_url": reverse("memo:present", args=[file_pk]),
            "export_pdf_url": reverse("memo:export_pdf", args=[file_pk]),
            "steven_surface": assistant.surface_for_file("memo", vault_file),
            "steven_deck_surface": assistant.surface_for_file("memo-deck",
                                                              vault_file),
            **door.as_context(),
            **BaseFileDisplayView.repo_context(vault_file, request.user),
        }, request)
        return render(request, self.template_name, context)


@require_POST
def presentation_save(request, file_pk):
    """Write the deck back to its file.

    Never refused for money — see toto.vault.editing. The refusals here are the
    ones a retry cannot fix by itself: a stranger (404), an encrypted file
    (403), a body too big (413) or unreadable (400), somebody else holding the
    lock (423), and a file that moved underneath this buffer (409, with the
    losing deck kept as a version).
    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Not authenticated."}, status=401)

    # The host-wide read-only switch, checked HERE and not only at the door.
    # `VAULT_FILE_EDITS = False` (faros sets it) means "refuse every
    # server-side rewrite of stored file content" — the vault's own API, the
    # vault views and the Core ACE editor all honour it at their save doors,
    # and this one did not, so a direct POST still rewrote the file. Same
    # 403 and the same wording as `toto.editor.views.save_file`.
    if not file_edits_allowed():
        return JsonResponse({"error": "File editing is disabled on this host."},
                            status=403)

    vault_file = _get_owned_file(request, file_pk)
    if vault_file.is_encrypted:
        return JsonResponse({"error": "File is encrypted. Decrypt it first."},
                            status=403)

    try:
        payload = _read_json_body(request, MAX_DECK_BYTES)
    except ValueError as exc:
        if str(exc) == "too-big":
            return JsonResponse(
                {"error": f"That deck is larger than "
                          f"{MAX_DECK_BYTES // (1024 * 1024)} MB. "
                          "Remove or shrink an image and try again."}, status=413)
        return JsonResponse({"error": f"Invalid JSON: {exc}"}, status=400)

    refusal = editing.refuse_if_locked(vault_file, request.user, noun="deck")
    if refusal is not None:
        return refusal

    try:
        presentation = presentation_format.Presentation.from_dict(
            payload.get("presentation") or payload)
        xml = presentation_format.dumps(presentation)
    except Exception as exc:                            # noqa: BLE001
        return JsonResponse({"error": f"Unreadable deck: {exc}"}, status=400)
    xml_bytes = xml.encode("utf-8")

    stale = editing.refuse_if_stale(
        vault_file, payload.get("base_hash"),
        body=xml_bytes, author=request.user, noun="deck")
    if stale is not None:
        return stale

    try:
        with vault_file.file.open("w") as fh:
            fh.write(xml)
        vault_file.content_hash = hashlib.sha256(xml_bytes).hexdigest()
        vault_file.file_size_bytes = len(xml_bytes)
        vault_file.save(update_fields=["content_hash", "file_size_bytes"])
    except Exception as exc:                            # noqa: BLE001
        return JsonResponse({"error": str(exc)}, status=500)

    settled = editing.settle(
        vault_file, request.user, metric_code=SAVE_METRIC,
        event_model=MemoUsageEvent, policy_model=MemoQuotaPolicy)

    return JsonResponse({"status": "ok",
                         "content_hash": vault_file.content_hash, **settled})


@login_required
@require_POST
def presentation_create(request):
    """A blank deck in the user's own space, opened for editing.

    The bytes come from `VaultEditorPlugin.blank_content`, the same place the
    vault's own "New file" menu gets them, rather than a second definition of
    "what does an empty deck look like" living here.
    """
    door = editing.door_for(request.user, entitlement=ENTITLEMENT,
                            metric_code=SAVE_METRIC, policy_model=MemoQuotaPolicy)
    if not door.open:
        return editing.closed_response(request, door)

    bucket, directory = resolve_new_file_target(
        request.user,
        request.POST.get("bucket_id"),
        request.POST.get("directory_id"),
    )
    raw = (request.POST.get("filename") or "").strip()
    base = raw[:-5] if raw.lower().endswith(".pxml") else raw
    base = base.strip() or "untitled-deck"
    vault_file = create_empty_vault_file(
        request.user, bucket, directory, f"{base}.pxml", "pxml")

    versions.save_version(vault_file, author=request.user, label="created")
    return redirect(reverse("memo:edit", args=[vault_file.pk]))


@login_required
@require_POST
def presentation_delete(request, file_pk):
    """Move a deck to the vault's trash (2026-10-01): its bytes and versions
    stay for the restore; a mounted remote bucket's deck goes at once."""
    from toto.vault.trash import remove_file

    vault_file = _get_owned_file(request, file_pk)
    remove_file(vault_file, by=request.user, request=request, door="memo_delete")
    return redirect(reverse("memo:index"))


@login_required
def presentation_media_embed(request):
    """An embeddable payload for a vault image or SVG the user may read.

    `GET ?file_pk=<pk>` answers `{"kind": "svg", "markup": …}` for an SVG or
    `{"kind": "image", "data_uri": …}` for a raster. The caller inlines the
    result into the slide, which is what keeps a deck self-contained.
    """
    try:
        file_pk = int(request.GET.get("file_pk", ""))
    except (TypeError, ValueError):
        return JsonResponse({"error": "file_pk is required."}, status=400)

    vault_file = get_object_or_404(
        accessible_files(request.user, file_types=_MEDIA_TYPES)
        .filter(is_encrypted=False),
        pk=file_pk,
    )
    try:
        with vault_file.file.open("rb") as fh:
            raw = fh.read()
    except Exception as exc:                            # noqa: BLE001
        return JsonResponse({"error": f"Could not read file: {exc}"}, status=500)

    alt = (vault_file.title or vault_file.key or "image").rsplit(".", 1)[0]
    if vault_file.file_type == "svg":
        return JsonResponse({
            "kind": "svg",
            "markup": clean_svg_markup(raw.decode("utf-8", errors="replace")),
            "alt": alt,
        })
    mime, _unused = mimetypes.guess_type(vault_file.title or vault_file.key or "")
    return JsonResponse({
        "kind": "image",
        "data_uri": image_bytes_to_data_uri(raw, mime or ""),
        "alt": alt,
    })


@login_required
@require_POST
def presentation_media_upload(request):
    """Embed a file dropped onto a slide, or picked with the file input.

    The bytes go through the server rather than a canvas in the browser, on two
    counts. The resize policy stays in one place — `media.image_bytes_to_data_uri`,
    which the vault picker already uses — so the two paths cannot drift. And an
    SVG gets sanitised by code that cannot be skipped by posting here directly.
    """
    upload = request.FILES.get("file")
    if upload is None:
        return JsonResponse({"error": "No file."}, status=400)
    if upload.size > getattr(settings, "MEMO_MAX_UPLOAD_BYTES", 20 * 1024 * 1024):
        return JsonResponse({"error": "That file is too large to embed."},
                            status=413)

    raw = upload.read()
    name = upload.name or "image"
    alt = name.rsplit(".", 1)[0]

    mime, _unused = mimetypes.guess_type(name)
    if (mime or "") == "image/svg+xml" or name.lower().endswith(".svg"):
        return JsonResponse({
            "kind": "svg",
            "payload": clean_svg_markup(raw.decode("utf-8", errors="replace")),
            "alt": alt,
        })
    if not (mime or "").startswith("image/"):
        return JsonResponse({"error": "Only images and SVGs can be embedded."},
                            status=400)
    return JsonResponse({
        "kind": "image",
        "payload": image_bytes_to_data_uri(raw, mime or ""),
        "alt": alt,
    })
