from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from toto.fileservices.access import user_can_access_vault_file
from toto.fileservices.dispatch import create_service_run, dispatch_run
from toto.ui import PageProcessor
from toto.vault.models import VaultFile


def _run_selected(request, file_pks, file_type, service_key, args=""):
    """Create + dispatch a service run for each accessible file of the right type.

    Returns the list of runs created (for redirect decisions).
    """
    files = [
        f for f in VaultFile.objects.filter(pk__in=file_pks).select_related("bucket", "directory")
        if f.file_type == file_type and user_can_access_vault_file(request.user, f)
    ]
    runs = []
    for f in files:
        run = create_service_run(request.user, f, service_key, args)
        try:
            dispatch_run(run)
        except Exception as exc:
            messages.error(request, f"{f.title}: {exc}")
        runs.append(run)
    return runs


@login_required
def home(request):
    """Landing page: pick one or more audio files from a tree and transcribe."""
    from toto.vault.filetree import build_file_tree

    if request.method == "POST":
        language = request.POST.get("language", "").strip()
        runs = _run_selected(request, request.POST.getlist("file_pks"), "audio", "transcription", language)
        if not runs:
            messages.error(request, "Select at least one audio file.")
            return redirect("transcription:home")
        messages.success(request, f"Started transcription for {len(runs)} file(s).")
        if len(runs) == 1:
            return redirect("fileservices:run_detail", run_id=runs[0].id)
        return redirect("transcription:home")

    tree = build_file_tree(request.user, file_types=["audio"])
    context = PageProcessor().decorate({"tree": tree}, request)
    return render(request, "transcription/home.html", context)


@login_required
def run_page(request, file_pk):
    """Collect transcription options for an audio file and run it.

    Builder-style entry point reached by redirect from the vault service menu.
    Access is verified independently here.
    """
    vf = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory", "owner"), pk=file_pk
    )
    if not user_can_access_vault_file(request.user, vf):
        raise Http404
    if vf.file_type != "audio":
        raise Http404  # transcription is audio-only

    if request.method == "POST":
        language = request.POST.get("language", "").strip()
        run = create_service_run(request.user, vf, "transcription", language)
        try:
            dispatch_run(run)
        except Exception as exc:
            messages.error(request, f"Transcription failed: {exc}")
        else:
            messages.success(request, "Transcription started.")
        return redirect("fileservices:run_detail", run_id=run.id)

    context = PageProcessor().decorate({"vf": vf}, request)
    return render(request, "transcription/run_page.html", context)
