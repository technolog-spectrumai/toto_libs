from __future__ import annotations

import hashlib
import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.base import ContentFile
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.text import slugify
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from toto.celery_utils import celery_available
from toto.texlab.compile import compile_tex_to_pdf
from toto.texlab.models import CompileRun
from toto.texlab.workflow import ensure_compile_workflow
from toto.ui import PageProcessor
from toto.vault.models import VaultFile
from toto.vault.plugins import VaultEditorPlugin
from toto.vault.views import (
    _unique_file_key,
    new_file_picker_json,
    resolve_new_file_target,
)


def _editor_url(vault_file) -> str:
    """URL to edit a .tex/.bib file in the generic ACE editor (LaTeX/BibTeX
    highlighting), or "" if no editor plugin is installed."""
    plugin = VaultEditorPlugin.for_file_type(vault_file.file_type)
    try:
        return plugin.get_editor_url(vault_file) if plugin else ""
    except Exception:
        return ""

# Starter content for a fresh .tex document. The vault's "New file" seed
# (toto.vault.views.CreateEmptyFileView._INITIAL["latex"]) mirrors this literal.
BLANK_TEX_DOCUMENT = (
    "\\documentclass{article}\n"
    "\n"
    "\\begin{document}\n"
    "\n"
    "Hello, \\LaTeX!\n"
    "\n"
    "\\end{document}\n"
)


class WorkspaceIndexView(View):
    """LaTeX workspace: list .tex/.sty/.bib vault files the user can open.

    File-based successor of the old ``LatexWorkspace`` model UI — the vault
    is the single source of truth, mirroring ``toto.memo.PresentationIndexView``.
    """

    template_name = "texlab/index.html"

    def get(self, request):
        qs = VaultFile.objects.filter(file_type__in=["latex", "bib"]).select_related(
            "owner", "bucket", "directory"
        )
        if request.user.is_authenticated:
            qs = qs.filter(Q(is_public=True) | Q(owner=request.user))
        else:
            qs = qs.filter(is_public=True)
        qs = qs.order_by("-uploaded_at", "title")

        def _location(f):
            loc = f.bucket.name if f.bucket else "—"
            if f.directory:
                loc = f"{loc} / {f.directory.full_path()}"
            return loc

        documents = [
            {
                "title": f.title,
                "file_type": f.file_type,
                "owner": f.owner.username,
                "uploaded": f.uploaded_at,
                "location": _location(f),
                "is_owner": request.user.is_authenticated and f.owner_id == request.user.id,
                # Editing happens in the generic ACE editor (owner-scoped there too).
                "edit_url": _editor_url(f),
                # Only .tex compiles; the compile POST runs the dedicated workflow.
                "compile_url": reverse("texlab:compile_latex", args=[f.pk]) if f.file_type == "latex" else "",
            }
            for f in qs
        ]

        # Files that participate in LaTeX compilation (inputs: .tex/.bib/text/image;
        # output: compiled .pdf) — shown in the workspace "Files" modal with type + size.
        file_qs = VaultFile.objects.filter(
            file_type__in=["latex", "bib", "text", "image", "pdf"]
        ).select_related("bucket", "directory")
        if request.user.is_authenticated:
            file_qs = file_qs.filter(Q(is_public=True) | Q(owner=request.user))
        else:
            file_qs = file_qs.filter(is_public=True)
        file_qs = file_qs.order_by("bucket__name", "directory__name", "title")[:500]

        workspace_files = [
            {
                "title": f.title or f.key,
                "file_type": f.file_type,
                "size": f.file_size_bytes,
                "location": _location(f),
            }
            for f in file_qs
        ]

        buckets_json, directories_json = new_file_picker_json(request.user)
        context = PageProcessor().decorate(
            {
                "documents": documents,
                "workspace_files": workspace_files,
                "buckets_json": buckets_json,
                "directories_json": directories_json,
            },
            request,
        )
        return render(request, self.template_name, context)


class WorkspaceCreateView(LoginRequiredMixin, View):
    """Create a blank .tex document in a chosen bucket/directory (or the user's
    personal bucket root when none is picked) and drop straight into the editor.
    Mirrors memo's PresentationCreateView."""

    login_url = reverse_lazy("core:login")

    def post(self, request):
        bucket, directory = resolve_new_file_target(
            request.user,
            request.POST.get("bucket_id"),
            request.POST.get("directory_id"),
        )

        raw = (request.POST.get("filename") or "").strip()
        base = raw[:-4] if raw.lower().endswith(".tex") else raw
        base = base.strip() or "untitled-document"
        title = f"{base}.tex"
        key = _unique_file_key(slugify(base), bucket)

        tex_bytes = BLANK_TEX_DOCUMENT.encode("utf-8")

        vault_file = VaultFile(
            owner=request.user,
            title=title,
            key=key,
            file_type="latex",
            bucket=bucket,
            directory=directory,
            is_public=False,
        )
        vault_file.file.save(title, ContentFile(tex_bytes), save=False)
        vault_file.content_hash = hashlib.sha256(tex_bytes).hexdigest()
        vault_file.file_size_bytes = len(tex_bytes)
        vault_file.save()

        # Editing happens in the generic ACE editor (LaTeX highlighting).
        return redirect(_editor_url(vault_file) or reverse("texlab:index"))


@csrf_exempt
def compile_latex(request, file_pk):
    """Compile a .tex vault file to PDF via the dedicated Celery workflow
    (slug ``texlab-compile-latex``). Editing is decoupled — this is the TeX
    Compiler entrypoint (the workspace's Compile button)."""
    vault_file = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory"),
        pk=file_pk,
    )

    run = CompileRun.objects.create(vault_file=vault_file, status=CompileRun.PENDING)

    if celery_available():
        from toto.workflows.models import WorkflowRun
        from toto.workflows.tasks import start_workflow_run_task

        wf = ensure_compile_workflow()
        wf_run = WorkflowRun.objects.create(
            workflow=wf,
            input_data={"data": {"vault_file_pk": vault_file.pk, "run_id": run.id}},
        )
        run.workflow_run = wf_run
        run.save(update_fields=["workflow_run"])
        start_workflow_run_task.delay(wf_run.pk)
        return JsonResponse({"status": "queued", "run_id": run.id, "workflow_run_id": wf_run.id})

    # Synchronous fallback
    run.status = CompileRun.RUNNING
    run.save(update_fields=["status"])
    try:
        pdf_vault, log = compile_tex_to_pdf(vault_file)
    except Exception as exc:
        run.status = CompileRun.FAILED
        run.log = str(exc)
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "log", "finished_at"])
        return JsonResponse({"status": "failed", "run_id": run.id, "log": run.log, "compiled_inline": True}, status=500)

    run.status = CompileRun.SUCCESS
    run.log = log
    run.pdf_url = pdf_vault.get_public_url()
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "log", "pdf_url", "finished_at"])
    return JsonResponse({"status": "ok", "run_id": run.id, "pdf_url": run.pdf_url, "compiled_inline": True})


def compile_status(request, run_id):
    run = get_object_or_404(CompileRun, id=run_id)
    return JsonResponse({
        "status": run.status,
        "log": run.log,
        "pdf_url": run.pdf_url,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    })
