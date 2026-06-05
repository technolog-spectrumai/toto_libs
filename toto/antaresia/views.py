from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from toto.celery_utils import celery_available
from toto.antaresia.models import PythonRun
from toto.antaresia.tasks import run_python_task
from toto.ui import PageProcessor
from toto.vault.models import VaultFile


class FileDisplayView(LoginRequiredMixin, View):
    template_name = "antaresia/file_display.html"
    login_url = reverse_lazy("core:login")

    def get(self, request, file_pk):
        vault_file = get_object_or_404(
            VaultFile.objects.select_related("bucket", "directory", "owner"),
            pk=file_pk,
            owner=request.user,
        )

        try:
            content = vault_file.file.read().decode("utf-8")
        except Exception:
            content = "[Unable to read file content]"

        runs = (
            PythonRun.objects
            .filter(vault_file=vault_file)
            .select_related("workflow_run")
            .order_by("-started_at")[:10]
        )

        directory_name = (
            vault_file.directory.name if vault_file.directory else vault_file.bucket.name
        )

        context = PageProcessor().decorate(
            {
                "vault_file": vault_file,
                "content": content,
                "runs": runs,
                "directory_name": directory_name,
            },
            request,
        )
        return render(request, self.template_name, context)


@csrf_exempt
def save_file(request, file_pk):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)

    vault_file = get_object_or_404(VaultFile, pk=file_pk, owner=request.user)
    content = request.POST.get("content", "")

    try:
        with vault_file.file.open("w") as f:
            f.write(content)
        vault_file.save()
        return JsonResponse({"status": "ok"})
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)


@csrf_exempt
def delete_file(request, file_pk):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)

    vault_file = get_object_or_404(VaultFile, pk=file_pk, owner=request.user)
    vault_file.file.delete(save=False)
    vault_file.delete()
    return JsonResponse({"status": "ok", "redirect": "/vault/"})


@csrf_exempt
def run_python(request, file_pk):
    vault_file = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory"),
        pk=file_pk,
    )

    if not celery_available():
        return JsonResponse({"error": "Celery worker is not available. Start the worker and try again."}, status=503)

    run = PythonRun.objects.create(vault_file=vault_file, status=PythonRun.PENDING)

    from toto.workflows.models import Workflow, WorkflowRun
    from toto.workflows.tasks import start_workflow_run_task

    wf = Workflow.objects.filter(slug="antaresia-run-python").first()
    if wf is not None:
        wf_run = WorkflowRun.objects.create(
            workflow=wf,
            input_data={"data": {"vault_file_pk": vault_file.pk, "run_id": run.id}},
        )
        run.workflow_run = wf_run
        run.save(update_fields=["workflow_run"])
        start_workflow_run_task.delay(wf_run.pk)
        return JsonResponse({"status": "queued", "run_id": run.id, "workflow_run_id": wf_run.id})

    run_python_task.delay(vault_file.pk, run.id)
    return JsonResponse({"status": "queued", "run_id": run.id})


def run_status(request, run_id):
    run = get_object_or_404(PythonRun, id=run_id)
    return JsonResponse({
        "status": run.status,
        "stdout": run.stdout,
        "stderr": run.stderr,
        "exit_code": run.exit_code,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    })


@login_required
def run_history_json(request, file_pk):
    vault_file = get_object_or_404(VaultFile, pk=file_pk, owner=request.user)
    runs = PythonRun.objects.filter(vault_file=vault_file).order_by("-started_at")[:20]
    return JsonResponse({
        "runs": [
            {
                "id": r.id,
                "status": r.status,
                "exit_code": r.exit_code,
                "started_at": r.started_at.isoformat(),
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "stdout_snippet": r.stdout[:200] if r.stdout else "",
                "stderr_snippet": r.stderr[:200] if r.stderr else "",
            }
            for r in runs
        ]
    })
