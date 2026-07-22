import os
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from toto.ui import PageProcessor

from .access import user_can_access_vault_file
from .commands import OPERATIONS, TAB_ORDER, commands_for_tab, get_command
from .models import FileJob

# Logical preset file-type → VaultFile.file_type values for the tree pickers.
_PRESET_TO_VAULT_TYPES = {
    "video": ["video"], "audio": ["audio"], "image": ["image"],
    "subtitle": ["text"], "gif": ["image"], "json": ["json"],
}

# Per-tab presentation + behaviour. ``ffmpeg`` keeps the command dropdown; the
# other three are single-command, focused tabs with a friendlier interface.
TAB_UI = {
    "ffmpeg": {
        "label": "ffmpeg", "icon": "fa-film",
        "blurb": "Convert, trim, resize, watermark and combine media with ffmpeg.",
        "source_heading": "Source", "upload_accept": "",
    },
    "ffprobe": {
        "label": "ffprobe", "icon": "fa-circle-info",
        "blurb": "Inspect a media file's streams, codecs, duration and metadata. "
                 "Produces a .ffprobe.json file.",
        "source_heading": "Media file to inspect", "upload_accept": "audio/*,video/*,image/*",
    },
    "transcribe": {
        "label": "Transcribe", "icon": "fa-closed-captioning",
        "blurb": "Turn speech into a text transcript and .srt subtitles with Whisper.",
        "source_heading": "Audio to transcribe", "upload_accept": "audio/*",
    },
}

# The focused tabs let you upload a new file (to a bucket of your choice) instead
# of only picking an existing vault file. The ffmpeg tab keeps its existing flow.
_UPLOAD_TABS = {"ffprobe", "transcribe"}

# The single command behind each focused tab.
_TAB_OP = {"ffprobe": "probe", "transcribe": "transcribe"}

# Source file types each focused tab accepts (ffmpeg derives them from the op).
_TAB_SOURCE_TYPES = {
    "ffprobe": ["video", "audio", "image"],
    "transcribe": ["audio"],
}

# When a file arrives with no tab/op (e.g. from the vault wand), route it to the
# most useful tab for its type.
_DEFAULT_TAB_BY_TYPE = {"video": "ffmpeg", "audio": "transcribe"}


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def display_input_name(vf) -> str:
    name = (getattr(vf, "title", "") or "").strip()
    return os.path.basename(name) or f"file{vf.pk}"


def _sanitize_output_name(value) -> str:
    base = os.path.basename(str(value or "").strip())
    base = re.sub(r"[^A-Za-z0-9 ._-]", "", base).strip()
    return base or "output"


def _primary_types(cmd_cls) -> list[str]:
    slots = list(cmd_cls.inputs.values())
    ftype = slots[0]["file_type"] if slots else "video"
    return _PRESET_TO_VAULT_TYPES.get(ftype, ["video"])


def _secondary_slot(cmd_cls):
    """(name, label, vault_types, multiple) for the extra picker, else None."""
    slots = list(cmd_cls.inputs.items())
    if len(slots) >= 2:
        name, slot = slots[1]
        return name, slot["name"], _PRESET_TO_VAULT_TYPES.get(slot["file_type"], []), False
    if slots and slots[0][1].get("multiple"):
        name, slot = slots[0]
        return name, slot["name"], _PRESET_TO_VAULT_TYPES.get(slot["file_type"], []), True
    return None


def _form_errors(form) -> list[str]:
    out = [str(e) for e in form.non_field_errors()]
    for field, errs in form.errors.items():
        if field == "__all__":
            continue
        out += [f"{field}: {e}" for e in errs]
    return out


def _build_form(cmd_cls, data=None):
    fc = cmd_cls.form_class
    return fc(data) if fc is not None else None


def _resolve_extra_pks(user, vf, pks):
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


def _validate(request, vf, cmd_cls):
    """Returns (params, extra_objs, extra_names, errors)."""
    form = _build_form(cmd_cls, request.POST)
    errors, params = [], {}
    if form is not None:
        if form.is_valid():
            params = {k: v for k, v in form.cleaned_data.items()
                      if k not in ("extra_files", "extra_file_ids")}
            if "output_name" in params:
                params["output_name"] = _sanitize_output_name(params["output_name"])
        else:
            errors.extend(_form_errors(form))

    objs, names = [], []
    slot = _secondary_slot(cmd_cls)
    if slot is not None:
        sname, label, _t, multiple = slot
        pks = request.POST.getlist(sname) or request.POST.getlist("extra_files")
        objs, names, ref_errors = _resolve_extra_pks(request.user, vf, pks)
        errors.extend(ref_errors)
        if multiple and len(objs) < 1:
            errors.append(f"Select at least one {label.lower()}.")
        elif not multiple and len(objs) != 1:
            errors.append(f"Select exactly one {label.lower()}.")
    return params, objs, names, errors


def _user_upload_buckets(user):
    """Buckets the user can upload into via the vault upload API (owner-matched)."""
    from toto.vault.models import Bucket

    return list(Bucket.objects.filter(owner=user).order_by("name").values("slug", "name"))


def _resolve_tab(tab_req, op_req, vf):
    """Pick the active tab from explicit request, the op, or the file type."""
    if tab_req in TAB_UI:
        return tab_req
    if op_req in OPERATIONS:
        return get_command(op_req).tab
    if vf is not None:
        return _DEFAULT_TAB_BY_TYPE.get(vf.file_type, "ffmpeg")
    return "ffmpeg"


def _resolve_op(tab, op_req):
    """The op for the tab: fixed for focused tabs, dropdown-chosen for ffmpeg."""
    if tab != "ffmpeg":
        return _TAB_OP[tab]
    if op_req in OPERATIONS and get_command(op_req).tab == "ffmpeg":
        return op_req
    return "compress"


@login_required
def command_builder(request):
    from toto.vault.filetree import build_file_tree
    from toto.vault.models import VaultFile

    tab_req = (request.POST.get("tab") or request.GET.get("tab") or "").strip()
    op_req = (request.POST.get("op") or request.GET.get("op") or "").strip()
    file_pk = request.POST.get("file") or request.GET.get("file")

    # Resolve the source first so we can default the tab from its type.
    vf, source_error = None, None
    if file_pk:
        vf = VaultFile.objects.select_related("bucket", "directory", "owner").filter(pk=file_pk).first()
        if vf is None or not user_can_access_vault_file(request.user, vf):
            vf, source_error = None, "Pick a file you can access."

    tab = _resolve_tab(tab_req, op_req, vf)
    op = _resolve_op(tab, op_req)
    cmd_cls = get_command(op)

    allowed_types = _TAB_SOURCE_TYPES.get(tab) or _primary_types(cmd_cls)
    if vf is not None and vf.file_type not in allowed_types:
        vf, source_error = None, f"This command needs a {' / '.join(allowed_types)} file."

    # Bare landing (no tab/op/source yet) → show every accessible media file and
    # route the click to the right tab by type; otherwise filter to this tab.
    browsing = vf is None and not tab_req and not op_req
    tree_types = ["video", "audio", "image"] if browsing else allowed_types
    source_tree = build_file_tree(request.user, file_types=tree_types)
    out_slot = next(iter(cmd_cls.outputs.values()), {})

    if browsing:
        source_link_prefix = "?file="
        source_types = "media"
    elif tab == "ffmpeg":
        source_link_prefix = f"?tab=ffmpeg&op={op}&file="
        source_types = " / ".join(allowed_types)
    else:
        source_link_prefix = f"?tab={tab}&file="
        source_types = " / ".join(allowed_types)

    allow_upload = tab in _UPLOAD_TABS
    upload_buckets = _user_upload_buckets(request.user) if allow_upload else []

    quick_languages = []
    if tab == "transcribe":
        from .commands.transcribe import LANGUAGE_CHOICES
        quick_languages = LANGUAGE_CHOICES

    context = {
        "vf": vf,
        "op": op,
        "tab": tab,
        "tabs": [dict(key=k, active=(k == tab), **TAB_UI[k]) for k in TAB_ORDER],
        "tab_ui": TAB_UI[tab],
        "operations": [(c.key, c.label) for c in commands_for_tab("ffmpeg")],
        "backend": (cmd_cls.backend_label or cmd_cls.backend),
        "is_service": cmd_cls.backend == "service",
        "source_tree": source_tree,
        "source_types": source_types,
        "source_link_prefix": source_link_prefix,
        "needs_extra": False,
        "extra_slot": None,
        "extra_tree": [],
        "form": None,
        "output_label": out_slot.get("name", "Output"),
        "errors": [source_error] if source_error else [],
        "allow_upload": allow_upload,
        "upload_buckets": upload_buckets,
        "upload_accept": TAB_UI[tab]["upload_accept"],
        "quick_languages": quick_languages,
    }

    if vf is None:
        return _render(request, "manta/command_builder.html", context)

    slot = _secondary_slot(cmd_cls)
    if slot is not None:
        sname, label, types, multiple = slot
        context["needs_extra"] = True
        context["extra_slot"] = {"name": sname, "label": label, "multiple": multiple,
                                 "types": " / ".join(types)}
        if vf.bucket_id:
            context["extra_tree"] = build_file_tree(
                request.user, file_types=types, bucket=vf.bucket, exclude_pk=vf.pk)

    if request.method == "POST":
        action = request.POST.get("action", "preview")
        params, objs, names, errors = _validate(request, vf, cmd_cls)

        # AJAX live preview — JSON only, never creates a job.
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            if errors:
                return JsonResponse({"ok": False, "errors": errors})
            cmd = cmd_cls()
            if cmd_cls.backend == "service":
                outs = [f"{s['name']} (.{s['extension']})" for s in cmd_cls.outputs.values()]
                return JsonResponse({"ok": True, "backend": (cmd_cls.backend_label or cmd_cls.backend),
                                     "command": cmd.describe(input_name=display_input_name(vf), params=params),
                                     "outputs": outs})
            spec = cmd.build_spec(input_name=display_input_name(vf), extra_input_names=names, params=params)
            return JsonResponse({"ok": True, "backend": (cmd_cls.backend_label or cmd_cls.backend),
                                 "command": spec.shell_display, "outputs": list(spec.output_names)})

        if action == "run" and not errors:
            return _run(request, vf, cmd_cls, params, objs)

        context["form"] = _build_form(cmd_cls, request.POST)
        context["errors"] = errors
        return _render(request, "manta/command_builder.html", context)

    context["form"] = _build_form(cmd_cls)
    return _render(request, "manta/command_builder.html", context)


def _run(request, vf, cmd_cls, params, extra_objs):
    # The command family owns execution; we just create the record and enqueue.
    job = FileJob.objects.create(
        name=f"{cmd_cls.label}: {vf.title}",
        command=cmd_cls.key,
        owner=request.user,
        inputs=[vf.id] + [o.id for o in extra_objs],
        params=dict(params),
        status=FileJob.Status.PENDING,
    )
    from .tasks_direct import run_direct_job
    run_direct_job.delay(job.id)
    messages.success(request, f"{cmd_cls.label} started.")
    return redirect("manta:job_detail", pk=job.id)


@login_required
def job_detail(request, pk):
    from toto.vault.models import VaultFile

    job = get_object_or_404(FileJob, pk=pk)
    out_files = list(VaultFile.objects.filter(pk__in=job.output_file_ids)) if job.output_file_ids else []
    return _render(request, "manta/job_detail.html", {"job": job, "out_files": out_files})


@login_required
def job_status(request, pk):
    """Lightweight poll endpoint so the job page can wait for an async command
    (ffmpeg / transcribe) and refresh itself when it finishes."""
    job = get_object_or_404(FileJob, pk=pk)
    return JsonResponse({
        "status": job.status,
        "status_display": job.get_status_display(),
        "is_terminal": job.is_terminal,
    })


def _save_text_file(user, bucket, directory, filename: str, text: str):
    """Persist *text* as a new text VaultFile in *bucket*, picking a free key
    (transcript, transcript-1, …) so repeated saves never overwrite. The key is
    set explicitly so it stays predictable even if storage suffixes the file."""
    from django.core.files.base import ContentFile
    from django.utils.text import slugify
    from toto.vault.models import VaultFile

    base, ext = os.path.splitext(os.path.basename(filename))
    base, ext = (base or "transcript"), (ext or ".txt")
    base_key = slugify(base) or "transcript"

    name, key, n = f"{base}{ext}", base_key, 1
    while VaultFile.objects.filter(bucket=bucket, key=key).exists():
        name, key, n = f"{base}-{n}{ext}", f"{base_key}-{n}", n + 1

    vf = VaultFile(owner=user, title=name, key=key, file_type="text",
                   bucket=bucket, directory=directory)
    vf.file.save(name, ContentFile(text.encode("utf-8")), save=False)
    vf.save()
    try:
        vf.content_hash = vf.create_hash()
        vf.save(update_fields=["content_hash"])
    except Exception:
        pass
    return vf


@login_required
def quick_transcribe(request):
    """Synchronously transcribe an already-saved audio file and write the result
    to a text file in the same bucket. Used by the transcribe tab's quick modal.

    POST: audio_id, output_name (default transcript.txt), language (optional).
    Returns JSON {ok, text, output:{id,title,download_url}}.
    """
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required."}, status=405)

    import tempfile

    from django.apps import apps as django_apps

    from toto.fileservices.runner import stage_input
    from toto.vault.models import VaultFile

    if not django_apps.is_installed("toto.transcription"):
        return JsonResponse(
            {"ok": False, "error": "Transcription is not available on this host."},
            status=400,
        )
    from toto.transcription.services import transcribe_demo_file

    audio = (VaultFile.objects.select_related("bucket", "directory")
             .filter(pk=request.POST.get("audio_id")).first())
    if audio is None or not user_can_access_vault_file(request.user, audio):
        return JsonResponse({"ok": False, "error": "Audio file not found or not accessible."}, status=404)
    # Recorded blobs are detected as audio (we name them .ogg/.weba/.m4a); video is
    # accepted too since Whisper can read it.
    if audio.file_type not in ("audio", "video"):
        return JsonResponse({"ok": False, "error": "That file is not audio."}, status=400)

    output_name = _sanitize_output_name(request.POST.get("output_name") or "transcript.txt")
    if not output_name.lower().endswith(".txt"):
        output_name += ".txt"
    language = (request.POST.get("language") or "").strip()

    try:
        with tempfile.TemporaryDirectory() as tmp:
            local = stage_input(audio, tmp)
            result = transcribe_demo_file(local, language=language)
    except Exception as exc:
        return JsonResponse({"ok": False, "error": f"Transcription failed: {exc}"}, status=500)

    text = (result.get("text") or "").strip()
    out_vf = _save_text_file(request.user, audio.bucket, audio.directory, output_name, text)
    try:
        download_url = request.build_absolute_uri(out_vf.file.url)
    except Exception:
        download_url = ""

    return JsonResponse({
        "ok": True,
        "text": text,
        "output": {"id": out_vf.id, "title": out_vf.title, "download_url": download_url},
    })
