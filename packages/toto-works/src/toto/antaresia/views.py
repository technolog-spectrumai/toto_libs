from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt

from toto.celery_utils import celery_available
from toto.editor.views import BaseFileDisplayView, delete_file, save_file  # noqa: F401 — re-exported
from toto.antaresia.models import PythonRun
from toto.antaresia.tasks import run_python_task
from toto.vault.models import VaultFile


class FileDisplayView(BaseFileDisplayView):
    template_name = "antaresia/file_display.html"
    ace_mode = "python"
    ws_path = "antaresia"
    save_url_name = "antaresia:save_file"
    delete_url_name = "antaresia:delete_file"

    def get_extra_context(self, vault_file):
        runs = (
            PythonRun.objects
            .filter(vault_file=vault_file)
            .select_related("workflow_run")
            .order_by("-started_at")[:10]
        )
        return {"runs": runs}


@csrf_exempt
def run_python(request, file_pk):
    vault_file = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory"),
        pk=file_pk,
    )
    if vault_file.is_encrypted:
        return JsonResponse({"error": "File is encrypted. Decrypt it first."}, status=403)

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
