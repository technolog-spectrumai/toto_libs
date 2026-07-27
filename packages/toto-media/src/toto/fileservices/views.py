from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from toto.ui import PageProcessor
from toto.vault.models import VaultFile
from .dispatch import create_service_run, dispatch_run
from .models import FileServiceRun
from .plugin import FileServicePlugin


# Primary "Open tool" target per file type. Video/audio go to the manta builder
# when it is installed (BUILD_MANTA). Images have no file-service tool — the vault
# file list offers an inline image preview instead.
# A missing plugin simply yields a 404 from open_primary_service — no crash.
PRIMARY_SERVICE_BY_TYPE = {"video": "manta", "audio": "manta"}


@login_required
def services_for_file(request, file_pk):
    """JSON list of services applicable to a given vault file."""
    vault_file = get_object_or_404(VaultFile, pk=file_pk)
    services = [p.to_dict() for p in FileServicePlugin.for_file(vault_file)]
    return JsonResponse({"services": services, "file_title": vault_file.title})


@login_required
def open_primary_service(request, file_pk):
    """Redirect straight to the primary tool for this file type (no modal)."""
    vault_file = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory"), pk=file_pk,
    )
    from .access import user_can_access_vault_file
    if not user_can_access_vault_file(request.user, vault_file):
        raise Http404
    key = PRIMARY_SERVICE_BY_TYPE.get(vault_file.file_type)
    plugin = FileServicePlugin.get(key) if key else None
    if plugin and plugin.builder:
        url = plugin.builder_url(vault_file)
        if url:
            return redirect(url)
    raise Http404


@csrf_exempt
@login_required
def run_service(request, file_pk):
    """Create a FileServiceRun for the chosen service and dispatch it."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)

    vault_file = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory"), pk=file_pk,
    )
    service_key = request.POST.get("service_key", "").strip()
    args = request.POST.get("args", "")

    plugin = FileServicePlugin.get(service_key)
    if plugin is None or not plugin.accepts(vault_file):
        return JsonResponse({"error": "Service is not available for this file."}, status=400)

    # Builder-backed services collect arguments on a dedicated app page; we just
    # verify access and hand the user off there.
    if plugin.builder:
        from .access import user_can_access_vault_file
        if not user_can_access_vault_file(request.user, vault_file):
            return JsonResponse({"error": "You do not have access to this file."}, status=403)
        url = plugin.builder_url(vault_file)
        if url:
            return JsonResponse({"status": "redirect", "redirect_url": url})

    if plugin.args_required and not args.strip():
        return JsonResponse({"error": f"{plugin.args_label} are required."}, status=400)

    run = create_service_run(request.user, vault_file, service_key, args)
    run_url = reverse("fileservices:run_detail", args=[run.id])
    try:
        queued = dispatch_run(run)
    except Exception as exc:
        return JsonResponse({"status": "failed", "run_id": run.id, "run_url": run_url,
                             "error": str(exc)}, status=200)
    if queued:
        return JsonResponse({"status": "queued", "run_id": run.id, "run_url": run_url})
    return JsonResponse({"status": "ok", "run_id": run.id, "run_url": run_url, "ran_inline": True})


def _run_payload(run: FileServiceRun) -> dict:
    return {
        "id": run.id,
        "service_key": run.service_key,
        "status": run.status,
        "stdout": run.stdout,
        "stderr": run.stderr,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "outputs": [
            {"pk": f.pk, "title": f.title, "file_type": f.file_type,
             "url": f.get_public_url() or ""}
            for f in run.output_files
        ],
    }


@login_required
def run_status(request, run_id):
    run = get_object_or_404(FileServiceRun, pk=run_id, owner=request.user)
    return JsonResponse(_run_payload(run))


class RunDetailView(LoginRequiredMixin, View):
    template_name = "fileservices/run_detail.html"
    login_url = reverse_lazy("core:login")

    def get(self, request, run_id):
        run = get_object_or_404(
            FileServiceRun.objects.select_related("input_file", "bucket", "workflow_run"),
            pk=run_id, owner=request.user,
        )
        plugin = FileServicePlugin.get(run.service_key)
        context = PageProcessor().decorate(
            {
                "run": run,
                "service_title": plugin.get_title() if plugin else run.service_key,
                "service_icon": plugin.icon if plugin else "fa-solid fa-wand-magic-sparkles",
                "status_url": reverse("fileservices:run_status", args=[run.id]),
                "outputs": run.output_files,
            },
            request,
        )
        return render(request, self.template_name, context)
