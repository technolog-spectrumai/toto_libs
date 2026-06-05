import json
import mimetypes
import os
import re

import yaml
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor

from .access import accessible_bucket_files, user_can_access_vault_file
from .factory import (
    FFmpegCommandFactory, OPERATIONS, OPERATION_LABELS, SECONDARY_OPERATIONS,
)
from .forms import (
    CompressForm, CutForm, ExtractMp3Form,
    GifForm, ResizeForm, ThumbnailForm, ConcatForm,
    CropForm, ChangeFpsForm, RemoveAudioForm, ReplaceAudioForm,
    AddSubtitlesForm, AddWatermarkForm, VstackForm, HstackForm,
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


# ---------------------------------------------------------------------------
# Iterative command builder
# ---------------------------------------------------------------------------

# Form classes used purely to validate the parsed YAML for each operation.
_OP_FORMS = {
    "compress": CompressForm,
    "resize": ResizeForm,
    "crop": CropForm,
    "change_fps": ChangeFpsForm,
    "cut": CutForm,
    "extract_mp3": ExtractMp3Form,
    "remove_audio": RemoveAudioForm,
    "replace_audio": ReplaceAudioForm,
    "add_subtitles": AddSubtitlesForm,
    "add_watermark": AddWatermarkForm,
    "thumbnail": ThumbnailForm,
    "gif": GifForm,
    "vstack": VstackForm,
    "hstack": HstackForm,
    "concat": ConcatForm,
}

# Default params used to seed the YAML editor and to fill omitted keys.
_OP_DEFAULTS = {
    "compress":      {"quality": "medium", "output_name": "compressed"},
    "resize":        {"preserve_aspect_ratio": True, "width": 1280, "output_name": "resized"},
    "crop":          {"width": 640, "height": 480, "x": 0, "y": 0, "output_name": "cropped"},
    "change_fps":    {"fps": 30, "output_name": "fps"},
    "cut":           {"start_time": "00:00:00", "end_time": "00:00:30", "output_name": "clip"},
    "extract_mp3":   {"bitrate": "192k", "output_name": "audio"},
    "remove_audio":  {"output_name": "muted"},
    "replace_audio": {"output_name": "dubbed", "extra_file_ids": []},
    "add_subtitles": {"output_name": "subtitled", "extra_file_ids": []},
    "add_watermark": {"position": "bottom-right", "output_name": "watermarked", "extra_file_ids": []},
    "thumbnail":     {"at_time": "00:00:01", "output_name": "thumbnail"},
    "gif":           {"start_time": "00:00:00", "duration": 5, "fps": 12, "width": 480, "output_name": "animation"},
    "vstack":        {"output_name": "vstack", "extra_file_ids": []},
    "hstack":        {"output_name": "hstack", "extra_file_ids": []},
    "concat":        {"reencode": False, "output_name": "merged", "extra_file_ids": []},
    "probe":         {},
}

# YAML keys that could smuggle a filesystem path are never trusted.
_PATH_KEYS = {
    "input", "inputs", "input_path", "input_paths", "input_file", "input_files",
    "output", "output_path", "source", "sources", "path", "paths", "file", "files",
}

_ROLE_HINTS = {
    "video": ["source", "concat input"],
    "audio": ["audio input"],
    "image": ["watermark / overlay", "thumbnail"],
    "text": ["subtitle"],
}


def display_input_name(vf) -> str:
    """A path-free, human display filename for a VaultFile.

    Never returns a real filesystem path — the factory builds the command
    preview from this, so it must not leak vault internals.
    """
    name = (getattr(vf, "title", "") or "").strip()
    base = os.path.basename(name)
    return base or f"file{vf.pk}"


def _sanitize_output_name(value) -> str:
    base = os.path.basename(str(value or "").strip())
    base = re.sub(r"[^A-Za-z0-9 ._-]", "", base).strip()
    return base or "output"


def _strip_path_keys(params: dict) -> dict:
    return {k: v for k, v in params.items() if k not in _PATH_KEYS}


def _default_yaml(op: str) -> str:
    return yaml.safe_dump(_OP_DEFAULTS.get(op, {}), sort_keys=False, default_flow_style=False)


def _form_errors(form) -> list[str]:
    out = []
    for err in form.non_field_errors():
        out.append(str(err))
    for field, errs in form.errors.items():
        if field == "__all__":
            continue
        for e in errs:
            out.append(f"{field}: {e}")
    return out


def _role_hints(file_type: str) -> list[str]:
    return _ROLE_HINTS.get(file_type, [])


def _bucket_file_row(f) -> dict:
    mime, _ = mimetypes.guess_type(f.title or "")
    return {
        "id": f.id,
        "title": f.title,
        "file_type": f.file_type,
        "mimetype": mime or "",
        "size": f.file_size_bytes,
        "roles": _role_hints(f.file_type),
    }


def _parse_and_validate(raw_yaml: str, op: str):
    """Parse the YAML spec and validate it against the operation form.

    Returns ``(cleaned_params, parsed_dict, errors)``. ``cleaned_params`` is
    ``None`` when there are errors.
    """
    try:
        parsed = yaml.safe_load(raw_yaml) if raw_yaml.strip() else {}
    except yaml.YAMLError as exc:
        return None, {}, [f"YAML parse error: {exc}"]
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        return None, {}, ["The operation spec must be a YAML mapping (key: value pairs)."]

    parsed = _strip_path_keys(parsed)

    if op == "probe":
        return {}, parsed, []

    form_cls = _OP_FORMS.get(op)
    if form_cls is None:
        return None, parsed, [f"Unknown operation: {op!r}"]

    defaults = _OP_DEFAULTS.get(op, {})
    data = {**defaults, **{k: v for k, v in parsed.items() if k != "extra_file_ids"}}
    if "output_name" in data:
        data["output_name"] = _sanitize_output_name(data["output_name"])

    form = form_cls(data=data, bucket_files=None) if op == "concat" else form_cls(data=data)
    if not form.is_valid():
        return None, parsed, _form_errors(form)

    cleaned = {k: v for k, v in form.cleaned_data.items() if k != "extra_file_ids"}
    return cleaned, parsed, []


def _resolve_extra_ids(user, vf, id_list):
    """Validate referenced VaultFile ids: existence, same bucket, access.

    Returns ``(ids, display_names, objects, errors)``.
    """
    from toto.vault.models import VaultFile

    ids, names, objs, errors = [], [], [], []
    seen = set()
    for raw in id_list:
        try:
            fid = int(raw)
        except (ValueError, TypeError):
            errors.append(f"Invalid file id: {raw!r}")
            continue
        if fid in seen:
            continue
        seen.add(fid)
        try:
            f = VaultFile.objects.select_related("bucket", "directory").get(pk=fid)
        except VaultFile.DoesNotExist:
            errors.append(f"Referenced file {fid} does not exist.")
            continue
        if f.bucket_id != vf.bucket_id:
            errors.append(f"File {fid} is not in this bucket and cannot be referenced.")
            continue
        if not user_can_access_vault_file(user, f):
            errors.append(f"You do not have access to file {fid}.")
            continue
        ids.append(fid)
        names.append(display_input_name(f))
        objs.append(f)
    return ids, names, objs, errors


@login_required
def command_builder(request, file_id):
    """Iterative ffmpeg/ffprobe command builder for a vault media file.

    GET shows the builder; POST ``action=preview`` renders the generated
    command without creating a job; POST ``action=run`` validates again and
    enqueues the job through the existing runner.
    """
    from toto.vault.models import VaultFile

    vf = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory", "owner"), pk=file_id
    )
    # Independent access check — do not trust the referring app.
    if not user_can_access_vault_file(request.user, vf):
        raise Http404

    service = request.GET.get("service", "").strip()
    op = (request.POST.get("op") or request.GET.get("op") or "").strip()
    if not op:
        op = "probe" if service == "ffprobe" else "compress"
    if op not in OPERATIONS:
        op = "compress"

    bucket_files = [
        _bucket_file_row(f)
        for f in accessible_bucket_files(
            request.user, vf.bucket,
            # text included so subtitle files (.srt/.vtt) can be picked for add_subtitles
            exclude_pk=vf.pk, file_types=["video", "audio", "image", "text"],
        ).order_by("title")[:200]
    ]

    spec_yaml = request.POST.get("spec_yaml")
    if spec_yaml is None:
        spec_yaml = _default_yaml(op)

    context = {
        "vf": vf,
        "op": op,
        "operations": [(o, OPERATION_LABELS[o]) for o in OPERATIONS],
        "bucket_files": bucket_files,
        "spec_yaml": spec_yaml,
        "selected_ids": [],
        "command_preview": None,
        "expected_outputs": [],
        "errors": [],
        "service": service,
    }

    if request.method == "POST":
        action = request.POST.get("action", "preview")
        checkbox_ids = request.POST.getlist("extra_file_ids")
        context["selected_ids"] = [str(s) for s in checkbox_ids]

        cleaned, parsed, errors = _parse_and_validate(spec_yaml, op)
        if errors:
            context["errors"] = errors
            return _render(request, "videomant/command_builder.html", context)

        # YAML extra_file_ids are canonical; merge in any checkbox selections.
        yaml_ids = parsed.get("extra_file_ids") or []
        if not isinstance(yaml_ids, (list, tuple)):
            context["errors"] = ["extra_file_ids must be a YAML list of file ids."]
            return _render(request, "videomant/command_builder.html", context)
        merged_ids = list(yaml_ids) + [c for c in checkbox_ids if c not in {str(y) for y in yaml_ids}]

        ids, extra_names, _objs, ref_errors = _resolve_extra_ids(request.user, vf, merged_ids)
        if ref_errors:
            context["errors"] = ref_errors
            context["selected_ids"] = [str(i) for i in ids]
            return _render(request, "videomant/command_builder.html", context)

        # Operations that consume extra inputs require the right file count.
        if op in SECONDARY_OPERATIONS and len(ids) != 1:
            context["errors"] = [
                "This operation needs exactly one additional file selected from the bucket."
            ]
            context["selected_ids"] = [str(i) for i in ids]
            return _render(request, "videomant/command_builder.html", context)
        if op == "concat" and len(ids) < 1:
            context["errors"] = ["Select at least one additional file to concatenate."]
            return _render(request, "videomant/command_builder.html", context)

        spec = FFmpegCommandFactory().build(
            op,
            input_name=display_input_name(vf),
            extra_input_names=extra_names,
            params=cleaned,
        )

        if action == "run":
            if op == "concat":
                input_files = [vf.id] + ids
            elif op in SECONDARY_OPERATIONS:
                input_files = [vf.id, ids[0]]
            else:
                input_files = []
            job = _make_job(request, f"videomant.{op}", vf, dict(cleaned), input_files=input_files)
            return _dispatch_and_redirect(request, job)

        context["command_preview"] = spec.shell_display
        context["expected_outputs"] = list(spec.output_names)
        context["selected_ids"] = [str(i) for i in ids]

    return _render(request, "videomant/command_builder.html", context)
