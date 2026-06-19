"""
Standalone OCR tool — pick an image from the vault, extract its text, and track
the run as a workflow. Replaces the manta "OCR" tab.

Execution reuses the shared file-service machinery: a FileServiceRun
(service_key="ocr") is created and dispatched through the generic
``fileservices-run`` workflow (see toto.fileservices.dispatch.dispatch_run),
so every OCR run shows up as a tracked WorkflowRun. The result page surfaces a
link to that run.
"""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from toto.ui import PageProcessor

from toto.fileservices.access import user_can_access_vault_file
from toto.fileservices.dispatch import create_service_run, dispatch_run
from toto.fileservices.models import FileServiceRun


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _user_upload_buckets(user):
    from toto.vault.models import Bucket

    return list(Bucket.objects.filter(owner=user).order_by("name").values("slug", "name"))


def _run_for(request, run_id) -> FileServiceRun:
    run = get_object_or_404(FileServiceRun, pk=run_id, service_key="ocr")
    if not (request.user.is_superuser or run.owner_id == request.user.id):
        raise Http404()
    return run


def _read_output_text(run) -> str:
    files = run.output_files
    if not files:
        return ""
    try:
        with files[0].file.open("rb") as fh:
            return fh.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def _workflow_run_url(run):
    if not run.workflow_run_id:
        return None
    try:
        return reverse("workflows:workflow_run_detail", args=[run.workflow_run_id])
    except Exception:
        return None


@login_required
def ocr_home(request):
    """Pick an image (or upload one), choose a language, and start OCR."""
    from toto.vault.filetree import build_file_tree
    from toto.vault.models import VaultFile

    file_pk = request.POST.get("file") or request.GET.get("file")
    vf, source_error = None, None
    if file_pk:
        vf = (VaultFile.objects.select_related("bucket", "directory", "owner")
              .filter(pk=file_pk).first())
        if vf is None or not user_can_access_vault_file(request.user, vf):
            vf, source_error = None, "Pick an image you can access."
        elif vf.file_type != "image":
            vf, source_error = None, "OCR needs an image file."

    if request.method == "POST" and vf is not None:
        language = (request.POST.get("language") or "").strip() or "eng"
        run = create_service_run(request.user, vf, "ocr", language)
        try:
            dispatch_run(run)
        except Exception as exc:
            messages.error(request, f"OCR failed to start: {exc}")
            return redirect(f"{reverse('ocr:home')}?file={vf.id}")
        return redirect("ocr:result", run_id=run.id)

    context = {
        "vf": vf,
        "source_error": source_error,
        "source_tree": build_file_tree(request.user, file_types=["image"]),
        "upload_buckets": _user_upload_buckets(request.user),
        "recent_runs": list(
            FileServiceRun.objects.filter(owner=request.user, service_key="ocr")[:12]
        ),
    }
    return _render(request, "ocr/ocr_home.html", context)


@login_required
def ocr_result(request, run_id):
    run = _run_for(request, run_id)
    text = _read_output_text(run) if run.status == FileServiceRun.SUCCESS else ""
    context = {
        "run": run,
        "is_terminal": run.status in (FileServiceRun.SUCCESS, FileServiceRun.FAILED),
        "text": text,
        "output_file": (run.output_files or [None])[0],
        "workflow_run_url": _workflow_run_url(run),
    }
    return _render(request, "ocr/ocr_result.html", context)


@login_required
def ocr_status(request, run_id):
    """Lightweight poll endpoint so the result page can wait for the async run."""
    run = _run_for(request, run_id)
    data = {
        "status": run.status,
        "status_display": run.get_status_display(),
        "is_terminal": run.status in (FileServiceRun.SUCCESS, FileServiceRun.FAILED),
        "workflow_run_url": _workflow_run_url(run),
    }
    if run.status == FileServiceRun.SUCCESS:
        data["text"] = _read_output_text(run)
    elif run.status == FileServiceRun.FAILED:
        data["error"] = (run.stderr or "")[:4000]
    return JsonResponse(data)
