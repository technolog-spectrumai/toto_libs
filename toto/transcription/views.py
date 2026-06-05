from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from toto.fileservices.access import user_can_access_vault_file
from toto.fileservices.dispatch import create_service_run, dispatch_run
from toto.ui import PageProcessor
from toto.vault.models import VaultFile


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
