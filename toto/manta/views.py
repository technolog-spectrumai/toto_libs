import os
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from toto.ui import PageProcessor

from .access import user_can_access_vault_file
from .commands import BaseCommand, OPERATIONS, get_command
from .models import FileJob

# Logical preset file-type → VaultFile.file_type values for the tree pickers.
_PRESET_TO_VAULT_TYPES = {
    "video": ["video"], "audio": ["audio"], "image": ["image"],
    "subtitle": ["text"], "gif": ["image"], "json": ["json"],
}


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


# When a file is picked with no command chosen, default the command by type.
_DEFAULT_OP_BY_TYPE = {"video": "compress", "audio": "transcribe", "image": "ocr"}


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


@login_required
def command_builder(request):
    from toto.vault.filetree import build_file_tree
    from toto.vault.models import VaultFile

    op_req = (request.POST.get("op") or request.GET.get("op") or "").strip()
    service = request.GET.get("service", "").strip()
    file_pk = request.POST.get("file") or request.GET.get("file")

    # Resolve the source first so we can default the command from its type.
    vf, source_error = None, None
    if file_pk:
        vf = VaultFile.objects.select_related("bucket", "directory", "owner").filter(pk=file_pk).first()
        if vf is None or not user_can_access_vault_file(request.user, vf):
            vf, source_error = None, "Pick a file you can access."

    op = op_req or (_DEFAULT_OP_BY_TYPE.get(vf.file_type, "compress") if vf else "compress")
    if op not in OPERATIONS:
        op = "compress"
    cmd_cls = get_command(op)

    primary_types = _primary_types(cmd_cls)
    if vf is not None and vf.file_type not in primary_types:
        vf, source_error = None, f"This command needs a {' / '.join(primary_types)} file."

    # Bare landing (no command and no source yet) → show every accessible media
    # file; once a command or source is in play, filter to the command's type.
    browsing = not op_req and vf is None
    tree_types = ["video", "audio", "image"] if browsing else primary_types
    source_tree = build_file_tree(request.user, file_types=tree_types)
    out_slot = next(iter(cmd_cls.outputs.values()), {})

    context = {
        "vf": vf,
        "op": op,
        "operations": [(c.key, c.label) for c in BaseCommand.all()],
        "backend": (cmd_cls.backend_label or cmd_cls.backend),
        "is_service": cmd_cls.backend == "service",
        "source_tree": source_tree,
        "source_types": "media" if browsing else " / ".join(primary_types),
        "source_link_prefix": "?file=" if browsing else f"?op={op}&file=",
        "needs_extra": False,
        "extra_slot": None,
        "extra_tree": [],
        "form": None,
        "output_label": out_slot.get("name", "Output"),
        "errors": [source_error] if source_error else [],
        "service": service,
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
