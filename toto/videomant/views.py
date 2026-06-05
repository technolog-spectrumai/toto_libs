import os
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor

from .access import accessible_bucket_files, user_can_access_vault_file
from .factory import (
    FFmpegCommandFactory, OPERATIONS, OPERATION_LABELS,
    get_command_input_slots, get_command_output_slots,
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

# Logical preset file-type → VaultFile.file_type values used by the tree pickers.
_PRESET_TO_VAULT_TYPES = {
    "video": ["video"],
    "audio": ["audio"],
    "image": ["image"],
    "subtitle": ["text"],   # .srt/.vtt are detected as VaultFile type "text"
    "gif": ["image"],
    "json": ["json"],
}


def _secondary_slot(op):
    """The extra-input picker for ``op``, derived from COMMAND_FILE_PRESETS.

    Returns ``(name, label, vault_types, multiple)`` or ``None`` for
    single-input operations.
    """
    slots = list(get_command_input_slots(op).items())
    if len(slots) >= 2:
        name, slot = slots[1]
        return name, slot["name"], _PRESET_TO_VAULT_TYPES.get(slot["file_type"], []), False
    if slots and slots[0][1].get("multiple"):
        name, slot = slots[0]
        return name, slot["name"], _PRESET_TO_VAULT_TYPES.get(slot["file_type"], []), True
    return None


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


def _form_errors(form) -> list[str]:
    out = [str(e) for e in form.non_field_errors()]
    for field, errs in form.errors.items():
        if field == "__all__":
            continue
        for e in errs:
            out.append(f"{field}: {e}")
    return out


def _build_op_form(op, data=None):
    """Instantiate the structured parameter form for ``op`` (None for probe)."""
    form_cls = _OP_FORMS.get(op)
    if form_cls is None:
        return None
    if op == "concat":
        return form_cls(data, bucket_files=None)
    return form_cls(data)


def _resolve_extra_pks(user, vf, pks):
    """Resolve picked file ids to accessible same-bucket VaultFiles.

    Re-checks access per file; out-of-bucket / unauthorized refs are rejected.
    Returns ``(objects, display_names, errors)``.
    """
    from toto.vault.models import VaultFile

    objs, names, errors, seen = [], [], [], set()
    for raw in pks:
        try:
            pk = int(raw)
        except (TypeError, ValueError):
            errors.append(f"Invalid file selection: {raw!r}")
            continue
        if pk in seen:
            continue
        seen.add(pk)
        f = VaultFile.objects.select_related("bucket", "directory").filter(pk=pk).first()
        if f is None:
            errors.append(f"Referenced file {pk} does not exist.")
            continue
        if f.bucket_id != vf.bucket_id:
            errors.append(f"File '{f.title}' is not in this bucket and cannot be used.")
            continue
        if not user_can_access_vault_file(user, f):
            errors.append(f"You do not have access to '{f.title}'.")
            continue
        objs.append(f)
        names.append(display_input_name(f))
    return objs, names, errors


def _builder_result(request, vf, op):
    """Validate the submitted form + file picks and build the command spec.

    Returns ``(spec, params, objs, errors)``; ``spec`` is None on error.
    """
    form = _build_op_form(op, request.POST)
    errors, params = [], {}
    if form is not None:
        if form.is_valid():
            params = {k: v for k, v in form.cleaned_data.items()
                      if k not in ("extra_files", "extra_file_ids")}
            if "output_name" in params:
                params["output_name"] = _sanitize_output_name(params["output_name"])
        else:
            errors.extend(_form_errors(form))

    slot = _secondary_slot(op)
    objs, extra_names = [], []
    if slot is not None:
        name, label, _types, multiple = slot
        pks = request.POST.getlist(name) or request.POST.getlist("extra_files")
        objs, extra_names, ref_errors = _resolve_extra_pks(request.user, vf, pks)
        errors.extend(ref_errors)
        if multiple and len(objs) < 1:
            errors.append(f"Select at least one {label.lower()}.")
        elif not multiple and len(objs) != 1:
            errors.append(f"Select exactly one {label.lower()}.")

    if errors:
        return None, params, objs, errors

    spec = FFmpegCommandFactory().build(
        op, input_name=display_input_name(vf),
        extra_input_names=extra_names, params=params,
    )
    return spec, params, objs, errors


def _resolve_source(request):
    """Resolve + validate the chosen source file. Returns (vf, error_or_None)."""
    from toto.vault.models import VaultFile

    file_pk = request.POST.get("file") or request.GET.get("file")
    if not file_pk:
        return None, None
    vf = (
        VaultFile.objects.select_related("bucket", "directory", "owner")
        .filter(pk=file_pk).first()
    )
    if vf is None or not user_can_access_vault_file(request.user, vf):
        return None, "Pick a video file you can access."
    if vf.file_type != "video":
        return None, "Videomant works on video files — pick a video."
    return vf, None


@login_required
def command_builder(request):
    """Form-based videomant builder, all on one page.

    1. Choose the operation.  2. Pick the source video (and any preset-declared
    extra file).  3. Fill the structured parameter form.  The generated ffmpeg
    command updates live (AJAX ``action=preview``). ``action=run`` validates and
    enqueues the job through the existing runner.
    """
    from toto.vault.filetree import build_file_tree

    vf, source_error = _resolve_source(request)
    service = request.GET.get("service", "").strip()
    op = (request.POST.get("op") or request.GET.get("op") or "").strip()
    if not op:
        op = "probe" if service == "ffprobe" else "compress"
    if op not in OPERATIONS:
        op = "compress"

    source_tree = build_file_tree(request.user, file_types=["video"])
    out_slot = next(iter(get_command_output_slots(op).values()), {})

    context = {
        "vf": vf,
        "op": op,
        "operations": [(o, OPERATION_LABELS[o]) for o in OPERATIONS],
        "source_tree": source_tree,
        "source_link_prefix": f"?op={op}&file=",  # picking a source keeps the op
        "needs_extra": False,
        "extra_slot": None,
        "extra_tree": [],
        "form": None,
        "output_label": out_slot.get("name", "Output"),
        "command_preview": None,
        "expected_outputs": [],
        "errors": [source_error] if source_error else [],
        "service": service,
    }

    # No (valid) source yet → just show the operation + source picker.
    if vf is None:
        return _render(request, "videomant/command_builder.html", context)

    # Preset-driven extra-input picker (named + type-filtered).
    slot = _secondary_slot(op)
    if slot is not None:
        name, label, types, multiple = slot
        context["needs_extra"] = True
        context["extra_slot"] = {"name": name, "label": label, "multiple": multiple,
                                 "types": ", ".join(types)}
        if vf.bucket_id:
            context["extra_tree"] = build_file_tree(
                request.user, file_types=types, bucket=vf.bucket, exclude_pk=vf.pk,
            )

    if request.method == "POST":
        action = request.POST.get("action", "preview")
        spec, params, objs, errors = _builder_result(request, vf, op)

        # AJAX live-preview: JSON only, never creates a job.
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            if spec is None:
                return JsonResponse({"ok": False, "errors": errors})
            return JsonResponse({"ok": True, "command": spec.shell_display,
                                 "outputs": list(spec.output_names)})

        if action == "run" and spec is not None:
            ids = [o.id for o in objs]
            if slot is not None and slot[3]:          # multiple inputs (concat)
                input_files = [vf.id] + ids
            elif slot is not None:                    # single secondary
                input_files = [vf.id, ids[0]]
            else:
                input_files = []
            job = _make_job(request, f"videomant.{op}", vf, dict(params), input_files=input_files)
            return _dispatch_and_redirect(request, job)

        context["form"] = _build_op_form(op, request.POST)
        context["errors"] = errors
        if spec is not None:
            context["command_preview"] = spec.shell_display
            context["expected_outputs"] = list(spec.output_names)
        return _render(request, "videomant/command_builder.html", context)

    # GET with a source → unbound form (command is filled live by JS).
    context["form"] = _build_op_form(op)
    return _render(request, "videomant/command_builder.html", context)
