from __future__ import annotations

import hashlib
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.views import View
from django.views.decorators.http import require_POST

from toto.core import assistant
from toto.ui import PageProcessor
from toto.vault import access, editing, versions
from toto.vault.models import VaultFile

log = logging.getLogger("toto.editor")


def _own_file(user, file_pk) -> VaultFile:
    """A file of this user's, or 404 — its bucket's clearances first.

    The owner filter alone let an owner who lacks their bucket's clearance
    open, save and delete there, against "no owner bypass" (2026-09-30).
    `access.gate_by_bucket` is the queryset the vault's own doors, memo and
    primula ask, so a file hidden by its bucket is missing here too.
    """
    files = VaultFile.objects.select_related("bucket", "directory", "owner")
    return get_object_or_404(access.gate_by_bucket(user, files), pk=file_pk, owner=user)


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

        vault_file = _own_file(request.user, file_pk)
        if vault_file.is_encrypted:
            return access.encrypted_lock_response(request, vault_file)

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


@require_POST
def save_file(request, file_pk):
    from toto.vault.models import file_edits_allowed
    if not file_edits_allowed():
        # Vault-level truth: even with the editor app installed, a host with
        # edits off must refuse the write.
        return JsonResponse({"error": "File editing is disabled on this host."}, status=403)

    # An unauthenticated caller reaches `owner=AnonymousUser` in the queryset
    # below, and comparing that to a foreign key raises ValueError — a 500 where
    # the honest answer is 401. Mostly masked now that CSRF refuses a tokenless
    # POST first, but "mostly" is not a guard. Cyprian answers this way too.
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Not authenticated."}, status=401)

    vault_file = _own_file(request.user, file_pk)
    if vault_file.is_encrypted:
        return JsonResponse({"error": "File is encrypted. Decrypt it first."}, status=403)

    # The lock is the first line, before the screen and before any write: a save
    # that should never have been attempted must not be able to fail halfway.
    # The owner filter above does not make this redundant — the vault lends a
    # file out through other surfaces, so the holder can be a collaborator in
    # cyprian or a shared directory rather than the owner sitting here.
    refusal = editing.refuse_if_locked(vault_file, request.user)
    if refusal is not None:
        return refusal

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

    encoded = content.encode("utf-8")

    # Optimistic concurrency, and deliberately AFTER the screen: a refused save
    # keeps the losing body as a version, and a version is storage — so the one
    # thing that must never be rescued into one is content the door just
    # refused. `base_hash` is optional, so a client that predates this (the
    # sketch SVG editor, the desktop API twin) keeps working exactly as before;
    # what it loses is only the protection it never had.
    stale = editing.refuse_if_stale(
        vault_file, request.POST.get("base_hash"),
        body=encoded, author=request.user)
    if stale is not None:
        return stale

    try:
        with vault_file.file.open("w") as f:
            f.write(content)
        # content_hash and file_size_bytes, not a bare save(): this path used to
        # leave both stale, so every hash-keyed thing downstream — the scan
        # verdict cache and its green tick among them — was reasoning about
        # bytes the file no longer held. Hashed from the string we were handed
        # rather than by re-reading the file, which is what the API twin does and
        # is the only version that still works after the write handle is closed.
        vault_file.content_hash = hashlib.sha256(encoded).hexdigest()
        vault_file.file_size_bytes = len(encoded)
        vault_file.save(update_fields=["content_hash", "file_size_bytes"])
        if verdict is not None:
            scanning.record(vault_file, verdict, user=request.user, door="editor")
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)

    # One version per save, and `versions.save_version` rather than
    # `editing.settle`: settle also counts the save and charges for it, and this
    # editor is a Standard, unmetered feature that must stay one.
    #
    # Guarded, because the bytes are already on disk. A history that could not
    # be written is not a reason to answer "your save failed" — the client would
    # keep a `base_hash` the file has already moved past, and every save after
    # that would look like somebody else's edit.
    payload = {"status": "ok", "content_hash": vault_file.content_hash}
    try:
        payload["version"] = versions.save_version(
            vault_file, author=request.user).number
    except Exception:                                   # noqa: BLE001
        log.exception("editor: could not version %s", vault_file.pk)
    return JsonResponse(payload)


@require_POST
def delete_file(request, file_pk):
    # No `csrf_exempt` on this door or on `save_file` above any more. It was
    # never needed: every client that reaches either of them — this app's own
    # editor page, the sketch SVG editor — already sends `X-CSRFToken`, and the
    # desktop client goes to the DRF twin in `toto.vault.api_views` with its own
    # authentication. What it bought was a permanent, unrecoverable delete of
    # somebody's file, reachable from any page they happened to be reading. The
    # vault's own DeleteFileView, which does exactly this from the file listing,
    # has always been protected; these were the doors that were not.
    # An unauthenticated caller reaches `owner=AnonymousUser` in the queryset
    # below, and comparing that to a foreign key raises ValueError — a 500 where
    # the honest answer is 401. Mostly masked now that CSRF refuses a tokenless
    # POST first, but "mostly" is not a guard. Cyprian answers this way too.
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Not authenticated."}, status=401)

    vault_file = _own_file(request.user, file_pk)
    # To the trash (2026-10-01), like the vault's own delete; a remote
    # bucket's file goes at once — this host cannot hold it for a restore.
    from toto.vault.trash import remove_file

    remove_file(vault_file, by=request.user, request=request, door="editor_delete")
    return JsonResponse({"status": "ok", "redirect": "/vault/"})


class TextFileDisplayView(BaseFileDisplayView):
    ace_mode = "text"
    ws_path = "editor"
    wrap_lines = True
    save_url_name = "editor:text_save"
    delete_url_name = "editor:text_delete"


class MarkdownFileDisplayView(BaseFileDisplayView):
    """Markdown in ACE, wrapped like prose rather than like code.

    `wrap_lines` matches TextFileDisplayView and not the code editors: a
    markdown paragraph is one long line by design, and horizontal scrolling
    through it is what makes people write hard-wrapped markdown instead.
    """

    ace_mode = "markdown"
    ws_path = "editor"
    wrap_lines = True
    save_url_name = "editor:markdown_save"
    delete_url_name = "editor:markdown_delete"


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

    #: The editor renders a live preview beside the source. Read by the
    #: template, which only draws the pane when a renderer is actually there.
    preview = True

    def get_extra_context(self, vault_file) -> dict:
        """An HTML file opens HERE, as HTML, with its rendering beside it.

        There used to be a second MODE on this page: a wand-toggle that swapped
        ACE for a TipTap pane over the same bytes and serialised back on save.
        It was removed because it was not an HTML editor — TipTap's schema has
        no node for `<head>`, `<style>`, a class or an inline style, so a round
        trip through it silently discarded all four. A rich-text editor that
        quietly rewrites the file it was given is worse than no rich-text
        editor. That reasoning is unchanged and the toggle is not coming back.

        There was also a link out to an explicit CTML conversion. CTML was
        retired on 2026-08-29, so there is nothing to convert to; a written
        document IS this file.

        What replaces both is a PREVIEW, which differs from an editor in the one
        way that matters: it never writes.

        THE PREVIEW IS CLIENT-SIDE, and deliberately so. It is a sandboxed
        iframe fed from the ACE buffer — no round trip, no CSRF, no server cost,
        and it updates as you type, which is the whole point of a preview. A
        browser rendering HTML is exactly the tool for the job; asking the
        server to render HTML into HTML would be ceremony.

        `toto.aralia` supplies the other half — the PDF — and that IS a server
        job, because WeasyPrint is what produces the file. So the button is
        aralia's and the pane is the browser's, and the split is honest: the
        pane shows what a browser makes of the page, the PDF shows what the
        renderer makes of it, and those are two different questions.

        Absent aralia there is still a preview and simply no PDF button. An
        editor that lost its preview because a renderer was missing would be a
        worse editor for no reason.
        """
        from django.apps import apps as django_apps
        from django.urls import NoReverseMatch, reverse

        if not django_apps.is_installed("toto.aralia"):
            return {"pdf_url": ""}
        try:
            return {"pdf_url": reverse("aralia:editor") + f"?file={vault_file.pk}"}
        except NoReverseMatch:
            # Installed but not mounted is a real shape on this platform. No
            # button rather than a 500 in the middle of somebody's editor.
            return {"pdf_url": ""}


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
