import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor

from .forms import (
    CompressForm, CutForm, ExtractMp3Form,
    GifForm, ResizeForm, ThumbnailForm, ConcatForm,
)
from .models import MediaJob


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _enqueue(job: MediaJob) -> None:
    from celery import current_app
    from .workflow import CELERY_TASK_REGISTRY
    celery_name = CELERY_TASK_REGISTRY[job.task_name]
    # For view-triggered jobs there is no workflow node_run; create a bare job run.
    # We dispatch using a lightweight wrapper task name that accepts a job id.
    current_app.send_task(f"{celery_name}_direct", args=[job.id])


def _enqueue_direct(job: MediaJob) -> None:
    """
    For jobs triggered from the UI (no workflow node_run),
    run the runner directly via a generic Celery task.
    """
    from .tasks_direct import run_direct_job
    result = run_direct_job.delay(job.id)
    job.status = MediaJob.Status.RUNNING
    job.save(update_fields=["status"])
    _ = result  # fire and forget; progress tracked via status.json


@login_required
def job_list(request):
    qs = MediaJob.objects.select_related("input_file", "output_file", "owner").order_by("-created_at")[:200]
    return _render(request, "videomant/job_list.html", {"jobs": qs})


@login_required
def job_detail(request, pk):
    job = get_object_or_404(MediaJob.objects.select_related("input_file", "output_file", "owner"), pk=pk)
    return _render(request, "videomant/job_detail.html", {"job": job})


@login_required
def job_status_json(request, pk):
    job = get_object_or_404(MediaJob, pk=pk)
    data = {
        "id": job.id,
        "status": job.status,
        "progress_percent": job.progress_percent,
        "progress_message": job.progress_message,
        "is_terminal": job.is_terminal,
        "output_file_id": job.output_file_id,
        "error_message": job.error_message,
        "stderr_tail": job.stderr[-2000:] if job.stderr else "",
    }
    return JsonResponse(data)


@login_required
def vault_file_actions(request, file_id):
    from django.urls import reverse
    from toto.vault.models import VaultFile
    vf = get_object_or_404(VaultFile, pk=file_id)
    form_data = [
        ("compress",    CompressForm(prefix="compress"),       reverse("videomant:enqueue_compress",    args=[file_id])),
        ("resize",      ResizeForm(prefix="resize"),           reverse("videomant:enqueue_resize",      args=[file_id])),
        ("cut",         CutForm(prefix="cut"),                 reverse("videomant:enqueue_cut",         args=[file_id])),
        ("extract_mp3", ExtractMp3Form(prefix="extract_mp3"), reverse("videomant:enqueue_extract_mp3", args=[file_id])),
        ("thumbnail",   ThumbnailForm(prefix="thumbnail"),    reverse("videomant:enqueue_thumbnail",   args=[file_id])),
        ("gif",         GifForm(prefix="gif"),                 reverse("videomant:enqueue_gif",         args=[file_id])),
        ("concat",      ConcatForm(prefix="concat"),           reverse("videomant:enqueue_concat",      args=[file_id])),
    ]
    return _render(request, "videomant/vault_file_actions.html", {"vf": vf, "form_data": form_data})


def _make_job(request, task_name: str, input_file, params: dict, input_files=None) -> MediaJob:
    return MediaJob.objects.create(
        task_name=task_name,
        owner=request.user,
        input_file=input_file,
        input_files=input_files or [],
        params=params,
    )


def _dispatch_and_redirect(request, job: MediaJob):
    from .tasks_direct import run_direct_job
    run_direct_job.delay(job.id)
    messages.success(request, f"Job #{job.id} ({job.task_name}) queued.")
    return redirect("videomant:job_detail", pk=job.id)


@require_POST
@login_required
def enqueue_compress(request, file_id):
    from toto.vault.models import VaultFile
    vf = get_object_or_404(VaultFile, pk=file_id)
    form = CompressForm(request.POST, prefix="compress")
    if not form.is_valid():
        messages.error(request, f"Invalid form: {form.errors}")
        return redirect("videomant:vault_file_actions", file_id=file_id)
    job = _make_job(request, "videomant.compress", vf, form.cleaned_data)
    return _dispatch_and_redirect(request, job)


@require_POST
@login_required
def enqueue_resize(request, file_id):
    from toto.vault.models import VaultFile
    vf = get_object_or_404(VaultFile, pk=file_id)
    form = ResizeForm(request.POST, prefix="resize")
    if not form.is_valid():
        messages.error(request, f"Invalid form: {form.errors}")
        return redirect("videomant:vault_file_actions", file_id=file_id)
    job = _make_job(request, "videomant.resize", vf, form.cleaned_data)
    return _dispatch_and_redirect(request, job)


@require_POST
@login_required
def enqueue_cut(request, file_id):
    from toto.vault.models import VaultFile
    vf = get_object_or_404(VaultFile, pk=file_id)
    form = CutForm(request.POST, prefix="cut")
    if not form.is_valid():
        messages.error(request, f"Invalid form: {form.errors}")
        return redirect("videomant:vault_file_actions", file_id=file_id)
    job = _make_job(request, "videomant.cut", vf, form.cleaned_data)
    return _dispatch_and_redirect(request, job)


@require_POST
@login_required
def enqueue_extract_mp3(request, file_id):
    from toto.vault.models import VaultFile
    vf = get_object_or_404(VaultFile, pk=file_id)
    form = ExtractMp3Form(request.POST, prefix="extract_mp3")
    if not form.is_valid():
        messages.error(request, f"Invalid form: {form.errors}")
        return redirect("videomant:vault_file_actions", file_id=file_id)
    job = _make_job(request, "videomant.extract_mp3", vf, form.cleaned_data)
    return _dispatch_and_redirect(request, job)


@require_POST
@login_required
def enqueue_thumbnail(request, file_id):
    from toto.vault.models import VaultFile
    vf = get_object_or_404(VaultFile, pk=file_id)
    form = ThumbnailForm(request.POST, prefix="thumbnail")
    if not form.is_valid():
        messages.error(request, f"Invalid form: {form.errors}")
        return redirect("videomant:vault_file_actions", file_id=file_id)
    job = _make_job(request, "videomant.thumbnail", vf, form.cleaned_data)
    return _dispatch_and_redirect(request, job)


@require_POST
@login_required
def enqueue_gif(request, file_id):
    from toto.vault.models import VaultFile
    vf = get_object_or_404(VaultFile, pk=file_id)
    form = GifForm(request.POST, prefix="gif")
    if not form.is_valid():
        messages.error(request, f"Invalid form: {form.errors}")
        return redirect("videomant:vault_file_actions", file_id=file_id)
    job = _make_job(request, "videomant.gif", vf, form.cleaned_data)
    return _dispatch_and_redirect(request, job)


@require_POST
@login_required
def enqueue_concat(request, file_id):
    from toto.vault.models import VaultFile
    vf = get_object_or_404(VaultFile, pk=file_id)
    form = ConcatForm(request.POST, prefix="concat")
    if not form.is_valid():
        messages.error(request, f"Invalid form: {form.errors}")
        return redirect("videomant:vault_file_actions", file_id=file_id)
    ids = form.cleaned_data["input_file_ids"]
    params = {k: v for k, v in form.cleaned_data.items() if k != "input_file_ids"}
    job = _make_job(request, "videomant.concat", vf, params, input_files=ids)
    return _dispatch_and_redirect(request, job)
