from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.views import View
from django.views.decorators.csrf import csrf_exempt

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

    def get_extra_context(self, vault_file) -> dict:
        return {}

    @staticmethod
    def gitvault_context(vault_file) -> dict:
        """Git toolbar context (commit/push/pull/history) when the file lives
        inside a git-enabled vault directory — {} otherwise. Shared by the
        other editor surfaces (antaresia/mandragora/memo) too."""
        from django.apps import apps as django_apps

        if not django_apps.is_installed("toto.gitvault"):
            return {}
        from toto.gitvault.integration import context_for_file

        ctx = context_for_file(vault_file)
        return {"gitvault_ctx": ctx} if ctx else {}

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
                **self.gitvault_context(vault_file),
                **self.get_extra_context(vault_file),
            },
            request,
        )
        return render(request, self.template_name, context)


@csrf_exempt
def save_file(request, file_pk):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)

    vault_file = get_object_or_404(VaultFile, pk=file_pk, owner=request.user)
    if vault_file.is_encrypted:
        return JsonResponse({"error": "File is encrypted. Decrypt it first."}, status=403)
    content = request.POST.get("content", "")

    try:
        with vault_file.file.open("w") as f:
            f.write(content)
        vault_file.save()
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
