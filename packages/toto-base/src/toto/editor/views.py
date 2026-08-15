from __future__ import annotations

import hashlib

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from toto.core import assistant
from toto.ui import PageProcessor
from toto.vault.models import VaultFile


class BaseFileDisplayView(LoginRequiredMixin, View):
    """
    Base view for file editors.  Subclasses set template_name, ace_mode,
    save_url_name, and delete_url_name; optionally override get_extra_context().
    """
    template_name = "editor/file_display.html"
    ace_mode: str = "text"
    ws_path: str = "editor"
    wrap_lines: bool = False
    save_url_name: str = ""
    delete_url_name: str = ""
    login_url = reverse_lazy("core:login")

    #: Which assistant surface a file type belongs to. Keyed on the FILE's type
    #: rather than the view class, because the two do not agree: `.py` has no
    #: view of its own and opens through `text_display`, so a class-level answer
    #: would offer prose actions on Python. A subclass may still override
    #: `steven_surface` to force one — SvgFileDisplayView sets "" because sketch
    #: owns drawings and a prose assistant on raw SVG is the wrong tool.
    #:
    #: The split that matters: an answer bound for an .html file is markup this
    #: platform renders and is screened like an upload; one bound for a .py or a
    #: .txt file is neither.
    STEVEN_SURFACE_BY_TYPE = {
        "python": "editor-code",
        "json": "editor-code",
        "yaml": "editor-code",
        "csv": "editor-code",
        "bib": "editor-code",
        "latex": "editor-latex",
        "html": "editor-markup",
        "xml": "editor-markup",
        "text": "editor-text",
    }

    #: Set on a subclass to override the table above. "" means never offered.
    steven_surface: str | None = None

    def resolve_steven_surface(self, vault_file) -> str:
        if self.steven_surface is not None:
            return self.steven_surface
        return self.STEVEN_SURFACE_BY_TYPE.get(vault_file.file_type, "editor-text")

    def get_extra_context(self, vault_file) -> dict:
        return {}

    @staticmethod
    def repo_context(vault_file, user) -> dict:
        """Git toolbar context (commit/push/pull/history) when the file lives
        inside a git-enabled vault directory AND the user passes the git
        access gate — {} otherwise. Shared by the editor surfaces
        (memo/cyprian) too."""
        from django.apps import apps as django_apps

        if not django_apps.is_installed("toto.repo"):
            return {}
        from toto.repo.integration import context_for_file

        ctx = context_for_file(vault_file, user)
        return {"repo_ctx": ctx} if ctx else {}

    def get(self, request, file_pk):
        from django.urls import reverse

        vault_file = get_object_or_404(
            VaultFile.objects.select_related("bucket", "directory", "owner"),
            pk=file_pk,
            owner=request.user,
        )
        if vault_file.is_encrypted:
            from toto.vault.access import encrypted_lock_response
            return encrypted_lock_response(request, vault_file)

        try:
            content = vault_file.file.read().decode("utf-8")
        except Exception:
            content = "[Unable to read file content]"

        directory_name = (
            vault_file.directory.name if vault_file.directory else vault_file.bucket.name
        )

        context = PageProcessor().decorate(
            {
                "vault_file": vault_file,
                "content": content,
                "directory_name": directory_name,
                "ace_mode": self.ace_mode,
                "ws_path": self.ws_path,
                "wrap_lines": "true" if self.wrap_lines else "false",
                "save_url": reverse(self.save_url_name, args=[file_pk]),
                "delete_url": reverse(self.delete_url_name, args=[file_pk]),
                # "" on a host without the assistant OR for a file whose
                # bucket carries the AI shield — the template renders nothing
                # at all. toto.core.assistant is the seam, so this app never
                # names the wheel that ships it.
                "steven_surface": assistant.surface_for_file(
                    self.resolve_steven_surface(vault_file), vault_file),
                # repo_context deliberately NOT merged here any more: the
                # generic editor is the vault's own surface and git left the
                # vault UI. The helper stays — memo and cyprian still call it.
                **self.get_extra_context(vault_file),
            },
            request,
        )
        return render(request, self.template_name, context)


@csrf_exempt
def save_file(request, file_pk):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)

    from toto.vault.models import file_edits_allowed
    if not file_edits_allowed():
        # Vault-level truth: even with the editor app installed, a host with
        # edits off must refuse the write.
        return JsonResponse({"error": "File editing is disabled on this host."}, status=403)

    vault_file = get_object_or_404(VaultFile, pk=file_pk, owner=request.user)
    if vault_file.is_encrypted:
        return JsonResponse({"error": "File is encrypted. Decrypt it first."}, status=403)
    content = request.POST.get("content", "")

    # Screen BEFORE anything is written. `scanning` is a façade over the
    # optional antivirus app: on a host without it this is clean-and-unscanned
    # and nothing changes. On a host with it, hostile content never reaches
    # storage — which is the whole point of doing it here rather than warning
    # about it afterwards.
    from toto.vault import scanning

    # The owner's preference gates this door (the queryset above makes the
    # actor the owner). Skipped means saved-but-unscanned, shown as exactly
    # that in the antivirus app.
    if scanning.should_scan(request.user, vault_file.file_type, door="editor"):
        verdict = scanning.scan(content, file_type=vault_file.file_type,
                                filename=vault_file.title)
        if not verdict.ok:
            scanning.record(vault_file, verdict, user=request.user, door="editor")
            return JsonResponse(verdict.as_error(), status=400)
    else:
        verdict = None

    try:
        with vault_file.file.open("w") as f:
            f.write(content)
        # content_hash and file_size_bytes, not a bare save(): this path used to
        # leave both stale, so every hash-keyed thing downstream — the scan
        # verdict cache and its green tick among them — was reasoning about
        # bytes the file no longer held. Hashed from the string we were handed
        # rather than by re-reading the file, which is what the API twin does and
        # is the only version that still works after the write handle is closed.
        encoded = content.encode("utf-8")
        vault_file.content_hash = hashlib.sha256(encoded).hexdigest()
        vault_file.file_size_bytes = len(encoded)
        vault_file.save(update_fields=["content_hash", "file_size_bytes"])
        if verdict is not None:
            scanning.record(vault_file, verdict, user=request.user, door="editor")
        return JsonResponse({"status": "ok"})
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)


@csrf_exempt
def delete_file(request, file_pk):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)

    vault_file = get_object_or_404(VaultFile, pk=file_pk, owner=request.user)
    vault_file.file.delete(save=False)
    vault_file.delete()
    return JsonResponse({"status": "ok", "redirect": "/vault/"})


class TextFileDisplayView(BaseFileDisplayView):
    ace_mode = "text"
    ws_path = "editor"
    wrap_lines = True
    save_url_name = "editor:text_save"
    delete_url_name = "editor:text_delete"


class JsonFileDisplayView(BaseFileDisplayView):
    ace_mode = "json"
    ws_path = "editor"
    save_url_name = "editor:json_save"
    delete_url_name = "editor:json_delete"


class YamlFileDisplayView(BaseFileDisplayView):
    ace_mode = "yaml"
    ws_path = "editor"
    save_url_name = "editor:yaml_save"
    delete_url_name = "editor:yaml_delete"


class SvgFileDisplayView(BaseFileDisplayView):
    steven_surface = ""
    ace_mode = "svg"
    ws_path = "editor"
    save_url_name = "editor:svg_save"
    delete_url_name = "editor:svg_delete"


class XmlFileDisplayView(BaseFileDisplayView):
    ace_mode = "xml"
    ws_path = "editor"
    save_url_name = "editor:xml_save"
    delete_url_name = "editor:xml_delete"


class HtmlFileDisplayView(BaseFileDisplayView):
    ace_mode = "html"
    ws_path = "editor"
    save_url_name = "editor:html_save"
    delete_url_name = "editor:html_delete"


class CsvFileDisplayView(BaseFileDisplayView):
    # Ace ships no CSV mode; plain-text highlighting is the correct fallback.
    ace_mode = "text"
    ws_path = "editor"
    wrap_lines = False
    save_url_name = "editor:csv_save"
    delete_url_name = "editor:csv_delete"


class LatexFileDisplayView(BaseFileDisplayView):
    # LaTeX source (.tex/.sty/.cls) edited with Ace's latex highlighting.
    # Compilation is the separate TeX Compiler workflow (toto.texlab) — when that
    # app is installed the toolbar gets a Compile button that dispatches it.
    ace_mode = "latex"
    ws_path = "editor"
    wrap_lines = True
    save_url_name = "editor:latex_save"
    delete_url_name = "editor:latex_delete"

    def get_extra_context(self, vault_file) -> dict:
        from django.apps import apps as django_apps
        from django.urls import NoReverseMatch, reverse

        if not django_apps.is_installed("toto.texlab"):
            return {}
        try:
            return {
                "compile_url": reverse("texlab:compile_latex", args=[vault_file.pk]),
                # Poll base — the JS swaps the "/0/" run-id segment per compile.
                "compile_status_url_base": reverse("texlab:compile_status", args=[0]),
            }
        except NoReverseMatch:
            return {}


class BibFileDisplayView(BaseFileDisplayView):
    # BibTeX bibliography (.bib) edited with Ace's bibtex highlighting.
    ace_mode = "bibtex"
    ws_path = "editor"
    save_url_name = "editor:bib_save"
    delete_url_name = "editor:bib_delete"
