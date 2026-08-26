import os
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from toto.ui import PageProcessor

from .access import user_can_access_vault_file
from .commands import OPERATIONS, TAB_ORDER, commands_for_tab, get_command
from toto.quota import QuotaExceeded, check_quota, record_usage
from toto.quota.charge import (InsufficientFunds, charge, check_funds,
                               price_for, refund_for)

from .models import MantaQuotaPolicy, MantaUsageEvent, FileJob

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
}

# The Time tab is not a command family: it renders the user's job-runtime
# dial. It appears only when the levy engine is mounted (dial()["set_url"]).
_TIME_TAB = {
    "label": "Time", "icon": "fa-clock",
    "blurb": "How long one of your jobs may run.",
}


def _time_dial(user):
    from toto.quota import times

    return times.dial("manta.job_runtime", user=user)


# The focused tabs let you upload a new file (to a bucket of your choice) instead
# of only picking an existing vault file. The ffmpeg tab keeps its existing flow.
_UPLOAD_TABS = {"ffprobe"}

# The single command behind each focused tab.
_TAB_OP = {"ffprobe": "probe"}

# Source file types each focused tab accepts (ffmpeg derives them from the op).
_TAB_SOURCE_TYPES = {
    "ffprobe": ["video", "audio", "image"],
}

# When a file arrives with no tab/op (e.g. from the vault wand), route it to the
# most useful tab for its type.
# Audio used to route to the transcribe tab; that app is parked (see the suite
# limbo/transcription/PARKED.md), and ffmpeg handles audio perfectly well.
_DEFAULT_TAB_BY_TYPE = {"video": "ffmpeg", "audio": "ffmpeg"}


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


def _anastasia_installed() -> bool:
    from django.apps import apps
    return apps.is_installed("toto.anastasia")


def _gear_preference(user) -> str:
    """The Gear this person last sent a job to, or "" for Automatic."""
    if not _anastasia_installed():
        return ""
    from .models import GearPreference
    return GearPreference.for_user(user)


def _gear_choices(user) -> list:
    """The Gears this person may send a job to, for the form. Empty on a host
    with no Compute Gears, and the control is then absent entirely."""
    if not _anastasia_installed():
        return []
    from toto.anastasia import jobs as gear_jobs
    return gear_jobs.gear_options(user)


@login_required
def command_builder(request):
    from toto.vault.filetree import build_file_tree
    from toto.vault.models import VaultFile

    tab_req = (request.POST.get("tab") or request.GET.get("tab") or "").strip()
    op_req = (request.POST.get("op") or request.GET.get("op") or "").strip()
    file_pk = request.POST.get("file") or request.GET.get("file")

    if tab_req == "time":
        dial = _time_dial(request.user)
        if dial.get("set_url"):
            tabs = [dict(key=k, active=False, **TAB_UI[k]) for k in TAB_ORDER]
            tabs.append(dict(key="time", active=True, **_TIME_TAB))
            return _render(request, "manta/time_tab.html",
                           {"tabs": tabs, "tab_ui": _TIME_TAB, "dial": dial})
        tab_req = ""  # no levy engine here — the tab does not exist

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

    context = {
        "vf": vf,
        "op": op,
        "tab": tab,
        "tabs": ([dict(key=k, active=(k == tab), **TAB_UI[k]) for k in TAB_ORDER]
                 + ([dict(key="time", active=False, **_TIME_TAB)]
                    if _time_dial(request.user).get("set_url") else [])),
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
        # Which Gears this person could send the job to. The form shows the
        # control only when there is a choice to make — see _command_form.html.
        "gear_choices": _gear_choices(request.user),
        # What they chose last time, so the answer they already gave is the one
        # already selected. "" is Automatic.
        "gear_selected": _gear_preference(request.user),
        "errors": [source_error] if source_error else [],
        "allow_upload": allow_upload,
        "upload_buckets": upload_buckets,
        "upload_accept": TAB_UI[tab]["upload_accept"],
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
    tariff = price_for(request.user, "manta")
    try:
        check_quota(MantaQuotaPolicy, "manta.job", 1, request.user)
        check_funds(request.user, tariff, "manta.job", 1)
    except (QuotaExceeded, InsufficientFunds) as exc:
        messages.error(request, str(exc))
        return redirect("manta:command_builder")

    # How long this job may run: the user's "manta.job_runtime" dial. Snapshot
    # it into params so the worker's subprocess ceiling matches the celery
    # limits fixed here, immune to mid-flight grant edits. The clamp keeps the
    # hard limit under the broker's visibility timeout, or Redis would
    # redeliver a task that is still legitimately running.
    from django.conf import settings as _settings

    from toto.quota import times

    budget = times.effective_seconds("manta.job_runtime", user=request.user) or 7200
    _visibility = (getattr(_settings, "CELERY_BROKER_TRANSPORT_OPTIONS", {})
                   .get("visibility_timeout") or 7800)
    budget = min(budget, _visibility - 200)

    # The command family owns execution; we just create the record and enqueue.
    # Which Gear, if the form said. Validated HERE rather than on the worker:
    # a refusal a person can act on belongs in the response to the button they
    # pressed, and a job row queued for a Gear that was never theirs is a
    # failure discovered a minute later by somebody who has left the page.
    gear_uuid = (request.POST.get("gear") or "").strip() or None
    if gear_uuid and _anastasia_installed():
        from toto.anastasia import jobs as gear_jobs
        try:
            gear_uuid = gear_jobs.require_gear(request.user, gear_uuid).uuid
        except gear_jobs.NoGear as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect("manta:command_builder")
    if _anastasia_installed():
        # Remembered only once the choice has been ACCEPTED above, so a refused
        # Gear is never the one waiting on the form next time. Automatic is
        # remembered too — going back to it has to stick like any other answer.
        from .models import GearPreference
        GearPreference.remember(request.user, gear_uuid)

    job = FileJob.objects.create(
        name=f"{cmd_cls.label}: {vf.title}",
        command=cmd_cls.key,
        owner=request.user,
        inputs=[vf.id] + [o.id for o in extra_objs],
        gear_uuid=gear_uuid,
        params={**dict(params), "time_budget_seconds": budget},
        status=FileJob.Status.PENDING,
    )
    _src = {"source_type": "manta.FileJob", "source_id": str(job.id)}
    if record_usage(MantaUsageEvent, "manta.job", 1, request.user,
                    idempotency_key=f"manta.job:{job.id}", **_src) is not None:
        charge(request.user, tariff, "manta.job", 1, **_src)

    from .tasks_direct import run_direct_job
    try:
        result = run_direct_job.apply_async(
            args=[job.id], soft_time_limit=budget, time_limit=budget + 100)
        FileJob.objects.filter(pk=job.id).update(celery_task_id=result.id or "")
    except Exception as exc:                      # noqa: BLE001 — broker died
        # Paid for, never queued. A job that RUNS and fails keeps its charge.
        refund_for("manta.FileJob", job.id, "manta.job", reason=f"not queued: {exc}")
        job.status = FileJob.Status.FAILED
        job.save(update_fields=["status"])
        messages.error(request, f"{cmd_cls.label} could not be queued: {exc}")
        return redirect("manta:job_detail", pk=job.id)
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
    and refresh itself when it finishes."""
    job = get_object_or_404(FileJob, pk=pk)
    return JsonResponse({
        "status": job.status,
        "status_display": job.get_status_display(),
        "is_terminal": job.is_terminal,
    })
