from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from toto.celery_utils import celery_available
from toto.texlab.compile import compile_tex_to_pdf
from toto.texlab.models import CompileRun
from toto.texlab.tasks import compile_latex_task
from toto.ui import PageProcessor
from toto.vault.models import VaultFile


class FileDisplayView(LoginRequiredMixin, View):
    template_name = "texlab/file_display.html"
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

        compile_runs = (
            CompileRun.objects
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
                "can_compile": vault_file.file_type == "latex",
                "compile_runs": compile_runs,
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
def compile_latex(request, file_pk):
    vault_file = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory"),
        pk=file_pk,
    )

    run = CompileRun.objects.create(vault_file=vault_file, status=CompileRun.PENDING)

    if celery_available():
        from toto.workflows.models import Workflow, WorkflowRun
        from toto.workflows.tasks import start_workflow_run_task

        wf = Workflow.objects.filter(slug="texlab-compile-latex").first()
        if wf is not None:
            wf_run = WorkflowRun.objects.create(
                workflow=wf,
                input_data={"data": {"vault_file_pk": vault_file.pk, "run_id": run.id}},
            )
            run.workflow_run = wf_run
            run.save(update_fields=["workflow_run"])
            start_workflow_run_task.delay(wf_run.pk)
            return JsonResponse({"status": "queued", "run_id": run.id, "workflow_run_id": wf_run.id})

        compile_latex_task.delay(vault_file.pk, run.id)
        return JsonResponse({"status": "queued", "run_id": run.id})

    # Synchronous fallback
    run.status = CompileRun.RUNNING
    run.save(update_fields=["status"])
    try:
        pdf_vault, log = compile_tex_to_pdf(vault_file)
    except Exception as exc:
        run.status = CompileRun.FAILED
        run.log = str(exc)
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "log", "finished_at"])
        return JsonResponse({"status": "failed", "run_id": run.id, "log": run.log, "compiled_inline": True}, status=500)

    run.status = CompileRun.SUCCESS
    run.log = log
    run.pdf_url = pdf_vault.get_public_url()
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "log", "pdf_url", "finished_at"])
    return JsonResponse({"status": "ok", "run_id": run.id, "pdf_url": run.pdf_url, "compiled_inline": True})


def compile_status(request, run_id):
    run = get_object_or_404(CompileRun, id=run_id)
    return JsonResponse({
        "status": run.status,
        "log": run.log,
        "pdf_url": run.pdf_url,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    })


@login_required
def compile_history_json(request, file_pk):
    vault_file = get_object_or_404(VaultFile, pk=file_pk, owner=request.user)
    runs = CompileRun.objects.filter(vault_file=vault_file).order_by("-started_at")[:20]
    return JsonResponse({
        "runs": [
            {
                "id": r.id,
                "status": r.status,
                "started_at": r.started_at.isoformat(),
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "pdf_url": r.pdf_url,
                "log_snippet": r.log[:300] if r.log else "",
            }
            for r in runs
        ]
    })


@login_required
def bucket_images_json(request, file_pk):
    vault_file = get_object_or_404(VaultFile, pk=file_pk, owner=request.user)
    images = VaultFile.objects.filter(
        bucket=vault_file.bucket,
        directory=vault_file.directory,
        file_type="image",
    ).order_by("title")
    return JsonResponse({
        "images": [{"title": img.title or img.key} for img in images]
    })
