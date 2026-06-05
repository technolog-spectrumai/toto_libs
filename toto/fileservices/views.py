from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from toto.celery_utils import celery_available
from toto.ui import PageProcessor
from toto.vault.models import VaultFile
from .models import FileServiceRun
from .plugin import FileServicePlugin


@login_required
def services_for_file(request, file_pk):
    """JSON list of services applicable to a given vault file."""
    vault_file = get_object_or_404(VaultFile, pk=file_pk)
    services = [p.to_dict() for p in FileServicePlugin.for_file(vault_file)]
    return JsonResponse({"services": services, "file_title": vault_file.title})


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
    if plugin.args_required and not args.strip():
        return JsonResponse({"error": f"{plugin.args_label} are required."}, status=400)

    run = FileServiceRun.objects.create(
        service_key=service_key,
        owner=request.user,
        input_file=vault_file,
        bucket=vault_file.bucket,
        args=args,
        status=FileServiceRun.PENDING,
    )

    run_url = reverse("fileservices:run_detail", args=[run.id])

    if celery_available():
        from .tasks import run_file_service_task
        from toto.workflows.models import Workflow, WorkflowRun
        from toto.workflows.tasks import start_workflow_run_task

        wf = Workflow.objects.filter(slug="fileservices-run").first()
        if wf is not None:
            wf_run = WorkflowRun.objects.create(
                workflow=wf,
                input_data={"data": {"run_id": run.id}},
            )
            run.workflow_run = wf_run
            run.save(update_fields=["workflow_run"])
            start_workflow_run_task.delay(wf_run.pk)
            return JsonResponse({"status": "queued", "run_id": run.id, "run_url": run_url,
                                 "workflow_run_id": wf_run.id})

        run_file_service_task.delay(run.id)
        return JsonResponse({"status": "queued", "run_id": run.id, "run_url": run_url})

    # Synchronous fallback when no worker is available.
    from .runner import execute_run
    try:
        execute_run(run.id)
    except Exception as exc:
        return JsonResponse({"status": "failed", "run_id": run.id, "run_url": run_url,
                             "error": str(exc)}, status=200)
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
