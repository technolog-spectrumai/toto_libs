"""
File-based presentation viewer + browser editor.

A presentation is a single self-contained ``.pml`` vault file
(``file_type="presentation"``) parsed by :mod:`toto.memo.presentation_format`.
These views never touch the database for presentation content — the vault file
is the single source of truth, mirroring the ``.tpy`` notebook editor in
:mod:`toto.mandragora.tpy_views`.
"""

from __future__ import annotations

import hashlib
import json

from django.contrib.auth.views import redirect_to_login
from django.core.files.base import ContentFile
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.mixins import LoginRequiredMixin

from toto.ui import PageProcessor
from toto.vault.models import VaultFile

from . import presentation_format


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_owned_file(request, file_pk) -> VaultFile:
    return get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory", "owner"),
        pk=file_pk,
        owner=request.user,
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
            VaultFile.objects.select_related("bucket", "directory", "owner"),
            pk=file_pk,
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

        # Owner gets an Edit link back into the browser editor.
        can_edit = request.user.is_authenticated and vault_file.owner == request.user

        context = PageProcessor().decorate(
            {
                "vault_file": vault_file,
                "presentation": presentation,
                "edit_url": reverse("memo:edit", args=[vault_file.pk]) if can_edit else "",
            },
            request,
        )
        return render(request, self.template_name, context)


# ---------------------------------------------------------------------------
# Source editor (plain text)
# ---------------------------------------------------------------------------

class PresentationSourceView(LoginRequiredMixin, View):
    """Plain-text editor for the raw presentation XML (owner only).

    This is what the vault's Edit button opens — in the vault a presentation
    is just an editable XML text file. The structured slide editor stays
    reachable from the memo app (index, player, and a toolbar link here).
    """

    template_name = "memo/source.html"
    login_url = reverse_lazy("core:login")

    def get(self, request, file_pk):
        vault_file = _get_owned_file(request, file_pk)
        try:
            content = vault_file.file.read().decode("utf-8")
        except Exception:
            content = ""

        context = PageProcessor().decorate(
            {
                "vault_file": vault_file,
                "content": content,
                "save_url": reverse("memo:source_save", args=[file_pk]),
                "edit_url": reverse("memo:edit", args=[file_pk]),
                "present_url": reverse("memo:present", args=[file_pk]),
            },
            request,
        )
        return render(request, self.template_name, context)


@csrf_exempt
def presentation_source_save(request, file_pk):
    """Persist raw XML text back to the vault file.

    No validation gate — the file is plain text and the viewer already
    tolerates corrupt content. Parse state is reported so the editor can
    warn without blocking the save.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Not authenticated."}, status=401)

    vault_file = _get_owned_file(request, file_pk)
    content = request.POST.get("content", "")
    content_bytes = content.encode("utf-8")

    try:
        with vault_file.file.open("w") as f:
            f.write(content)
        vault_file.content_hash = hashlib.sha256(content_bytes).hexdigest()
        vault_file.file_size_bytes = len(content_bytes)
        vault_file.save(update_fields=["content_hash", "file_size_bytes"])
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)

    try:
        presentation_format.loads(content)
        valid = True
    except presentation_format.PresentationParseError:
        valid = False
    return JsonResponse({"status": "ok", "valid_presentation": valid})


# ---------------------------------------------------------------------------
# Editor
# ---------------------------------------------------------------------------

class PresentationEditView(LoginRequiredMixin, View):
    """In-browser editor for a presentation vault file (owner only)."""

    template_name = "memo/edit.html"
    login_url = reverse_lazy("core:login")

    def get(self, request, file_pk):
        vault_file = _get_owned_file(request, file_pk)
        presentation = _read_presentation(vault_file)

        context = PageProcessor().decorate(
            {
                "vault_file": vault_file,
                # Hydration payload — the template emits this via {{ ...|json_script }},
                # which JSON-encodes the dict, so pass the dict (not a string).
                "presentation_json": presentation.to_dict(),
                "save_url": reverse("memo:save", args=[file_pk]),
                "present_url": reverse("memo:present", args=[file_pk]),
            },
            request,
        )
        return render(request, self.template_name, context)


@csrf_exempt
def presentation_save(request, file_pk):
    """Persist edited slides back to the vault file as presentation XML."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Not authenticated."}, status=401)

    vault_file = _get_owned_file(request, file_pk)

    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, TypeError) as exc:
        return JsonResponse({"error": f"Invalid JSON: {exc}"}, status=400)

    presentation = presentation_format.Presentation.from_dict(payload)
    xml = presentation_format.dumps(presentation)
    xml_bytes = xml.encode("utf-8")

    try:
        with vault_file.file.open("w") as f:
            f.write(xml)
        # Hash/size from the bytes we just wrote — the FieldFile is closed once
        # the write-context exits, so we can't re-read it here.
        vault_file.content_hash = hashlib.sha256(xml_bytes).hexdigest()
        vault_file.file_size_bytes = len(xml_bytes)
        vault_file.save(update_fields=["content_hash", "file_size_bytes"])
        return JsonResponse({"status": "ok"})
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

class PresentationCreateView(LoginRequiredMixin, View):
    """One-click: create a blank presentation in the user's personal bucket
    and drop straight into the editor."""

    login_url = reverse_lazy("core:login")

    def post(self, request):
        from toto.vault.models import Bucket

        bucket, _ = Bucket.objects.get_or_create(
            owner=request.user,
            slug=f"personal-{request.user.username}",
            defaults={
                "name": f"Personal — {request.user.username}",
                "storage_backend": "local",
            },
        )

        base_key = "untitled-presentation"
        key = base_key
        counter = 1
        while VaultFile.objects.filter(bucket=bucket, key=key).exists():
            key = f"{base_key}-{counter}"
            counter += 1

        xml = presentation_format.dumps(
            presentation_format.new_presentation("Untitled Presentation")
        )
        xml_bytes = xml.encode("utf-8")

        vault_file = VaultFile(
            owner=request.user,
            title=f"{key}.pml",
            key=key,
            file_type="presentation",
            bucket=bucket,
            is_public=False,
        )
        vault_file.file.save(f"{key}.pml", ContentFile(xml_bytes), save=False)
        vault_file.content_hash = hashlib.sha256(xml_bytes).hexdigest()
        vault_file.file_size_bytes = len(xml_bytes)
        vault_file.save()

        return redirect(reverse("memo:edit", args=[vault_file.pk]))


# ---------------------------------------------------------------------------
# Index / gallery
# ---------------------------------------------------------------------------

class PresentationIndexView(View):
    """List presentation vault files the current user can open."""

    template_name = "memo/index.html"

    def get(self, request):
        from django.db.models import Q

        qs = VaultFile.objects.filter(file_type="presentation").select_related(
            "owner", "bucket", "directory"
        )
        if request.user.is_authenticated:
            qs = qs.filter(Q(is_public=True) | Q(owner=request.user))
        else:
            qs = qs.filter(is_public=True)
        qs = qs.order_by("-uploaded_at", "title")

        presentations = [
            {
                "title": f.title,
                "owner": f.owner.username,
                "uploaded": f.uploaded_at,
                "is_owner": request.user.is_authenticated and f.owner_id == request.user.id,
                "present_url": reverse("memo:present", args=[f.pk]),
                "edit_url": reverse("memo:edit", args=[f.pk]),
                "source_url": reverse("memo:source", args=[f.pk]),
            }
            for f in qs
        ]

        context = PageProcessor().decorate(
            {"presentations": presentations}, request
        )
        return render(request, self.template_name, context)
