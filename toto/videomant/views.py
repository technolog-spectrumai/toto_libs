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
from .models import MediaJob, Workspace


def _is_federal_tribe_member(user) -> bool:
    try:
        return user.community_profile.communities.filter(is_federal_tribe=True).exists()
    except Exception:
        return False


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
def bucket_list(request):
    from toto.vault.models import Bucket
    from django.db.models import Count, Q
    buckets = (
        Bucket.objects
        .annotate(
            media_count=Count(
                "files",
                filter=Q(files__file_type__in=["video", "audio"]),
            )
        )
        .order_by("name")
    )
    return _render(request, "videomant/bucket_list.html", {"buckets": buckets})


@login_required
def bucket_file_list(request, bucket_pk):
    from toto.vault.models import Bucket, VaultFile
    bucket = get_object_or_404(Bucket, pk=bucket_pk)
    q = request.GET.get("q", "").strip()
    file_type = request.GET.get("type", "")
    qs = (
        VaultFile.objects
        .filter(bucket=bucket, file_type__in=["video", "audio", "image"])
        .select_related("directory")
        .order_by("-uploaded_at")
    )
    if q:
        qs = qs.filter(title__icontains=q)
    if file_type:
        qs = qs.filter(file_type=file_type)
    files = list(qs)
    recent_jobs_map = {
        j.input_file_id: j
        for j in MediaJob.objects.filter(input_file__in=files)
        .order_by("-created_at")
        .select_related("output_file")[:400]
    }
    file_rows = [(vf, recent_jobs_map.get(vf.id)) for vf in files]
    return _render(request, "videomant/bucket_file_list.html", {
        "bucket": bucket,
        "file_rows": file_rows,
        "q": q,
        "file_type": file_type,
        "total": len(files),
    })


@login_required
def workspace_list(request):
    qs = Workspace.objects.filter(
        owner=request.user
    ) | Workspace.objects.filter(
        allowed_users=request.user
    )
    qs = qs.distinct().select_related("bucket", "owner").order_by("-created_at")
    can_create = _is_federal_tribe_member(request.user) or request.user.is_superuser
    return _render(request, "videomant/workspace_list.html", {
        "workspaces": qs,
        "can_create": can_create,
    })


@login_required
def workspace_detail(request, slug):
    from toto.vault.models import VaultFile
    ws = get_object_or_404(Workspace, slug=slug)
    if not ws.user_has_access(request.user):
        from django.http import Http404
        raise Http404
    jobs = ws.jobs.select_related("input_file", "output_file").order_by("-created_at")[:50]
    bucket_files = (
        VaultFile.objects
        .filter(bucket=ws.bucket, file_type__in=["video", "audio", "image"])
        .order_by("-uploaded_at")[:100]
    )
    recent_jobs_map = {
        j.input_file_id: j
        for j in MediaJob.objects.filter(input_file__in=bucket_files, workspace=ws)
        .order_by("-created_at")[:200]
    }
    file_rows = [(vf, recent_jobs_map.get(vf.id)) for vf in bucket_files]
    return _render(request, "videomant/workspace_detail.html", {
        "ws": ws,
        "jobs": jobs,
        "file_rows": file_rows,
    })


@login_required
def workspace_create(request):
    if not (_is_federal_tribe_member(request.user) or request.user.is_superuser):
        messages.error(request, "Only federal tribe members can create workspaces.")
        return redirect("videomant:workspace_list")

    from toto.vault.models import Bucket
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        bucket_id = request.POST.get("bucket_id", "").strip()
        description = request.POST.get("description", "").strip()
        if not name or not bucket_id:
            messages.error(request, "Name and bucket are required.")
        else:
            try:
                bucket = Bucket.objects.get(pk=bucket_id)
            except Bucket.DoesNotExist:
                messages.error(request, "Bucket not found.")
                bucket = None
            if bucket:
                ws = Workspace.objects.create(
                    name=name,
                    description=description,
                    bucket=bucket,
                    owner=request.user,
                )
                messages.success(request, f"Workspace \"{ws.name}\" created.")
                return redirect("videomant:workspace_detail", slug=ws.slug)

    buckets = Bucket.objects.all().order_by("name")
    return _render(request, "videomant/workspace_create.html", {"buckets": buckets})


@login_required
def workspace_delete(request, slug):
    ws = get_object_or_404(Workspace, slug=slug, owner=request.user)
    if request.method == "POST":
        ws.delete()
        messages.success(request, "Workspace deleted.")
        return redirect("videomant:workspace_list")
    return redirect("videomant:workspace_detail", slug=slug)


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

    ws = None
    ws_slug = request.GET.get("ws", "").strip()
    if ws_slug:
        ws = get_object_or_404(Workspace, slug=ws_slug)
        if not ws.user_has_access(request.user):
            ws = None

    # Files from the workspace bucket available for concat (excluding the primary file)
    bucket_files = []
    if ws:
        bucket_files = list(
            VaultFile.objects
            .filter(bucket=ws.bucket, file_type__in=["video", "audio"])
            .exclude(pk=vf.pk)
            .order_by("title")[:200]
        )

    concat_form = ConcatForm(prefix="concat", bucket_files=bucket_files)
    form_data = [
        ("compress",    CompressForm(prefix="compress"),       reverse("videomant:enqueue_compress",    args=[file_id])),
        ("resize",      ResizeForm(prefix="resize"),           reverse("videomant:enqueue_resize",      args=[file_id])),
        ("cut",         CutForm(prefix="cut"),                 reverse("videomant:enqueue_cut",         args=[file_id])),
        ("extract_mp3", ExtractMp3Form(prefix="extract_mp3"), reverse("videomant:enqueue_extract_mp3", args=[file_id])),
        ("thumbnail",   ThumbnailForm(prefix="thumbnail"),    reverse("videomant:enqueue_thumbnail",   args=[file_id])),
        ("gif",         GifForm(prefix="gif"),                 reverse("videomant:enqueue_gif",         args=[file_id])),
        ("concat",      concat_form,                           reverse("videomant:enqueue_concat",      args=[file_id])),
    ]
    return _render(request, "videomant/vault_file_actions.html", {
        "vf": vf,
        "form_data": form_data,
        "ws": ws,
        "ws_slug": ws_slug,
    })


def _resolve_workspace(request) -> "Workspace | None":
    ws_slug = request.POST.get("ws_slug", "").strip()
    if not ws_slug:
        return None
    try:
        ws = Workspace.objects.get(slug=ws_slug)
        return ws if ws.user_has_access(request.user) else None
    except Workspace.DoesNotExist:
        return None


def _make_job(request, task_name: str, input_file, params: dict, input_files=None) -> MediaJob:
    ws = _resolve_workspace(request)
    return MediaJob.objects.create(
        task_name=task_name,
        owner=request.user,
        workspace=ws,
        input_file=input_file,
        input_files=input_files or [],
        params=params,
    )


def _dispatch_and_redirect(request, job: MediaJob):
    from .tasks_direct import run_direct_job
    run_direct_job.delay(job.id)
    messages.success(request, f"Job #{job.id} ({job.task_name}) queued.")
    if job.workspace_id:
        return redirect("videomant:workspace_detail", slug=job.workspace.slug)
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
        ws_slug = request.POST.get("ws_slug", "")
        url = f"/videomant/vault/{file_id}/actions/"
        if ws_slug:
            url += f"?ws={ws_slug}"
        return redirect(url)
    # Primary file is always first; selected extras follow
    extra_ids = form.cleaned_data["extra_file_ids"]
    all_ids = [vf.id] + extra_ids
    params = {k: v for k, v in form.cleaned_data.items() if k != "extra_file_ids"}
    job = _make_job(request, "videomant.concat", vf, params, input_files=all_ids)
    return _dispatch_and_redirect(request, job)
