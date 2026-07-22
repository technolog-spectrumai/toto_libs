from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor
from toto.vault.models import VaultFile

from .models import TexPlayJob


def latex_play(request, file_pk):
    vault_file = get_object_or_404(VaultFile, pk=file_pk)

    if vault_file.is_encrypted:
        return HttpResponseForbidden("Cannot compile an encrypted file.")

    if not vault_file.is_public:
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        can_access = vault_file.owner == request.user
        if not can_access and vault_file.directory:
            can_access = vault_file.directory.user_can_access(request.user)
        if not can_access:
            return HttpResponseForbidden()

    latest_job = (
        TexPlayJob.objects
        .filter(vault_file=vault_file)
        .select_related("pdf_vault_file")
        .first()
    )

    context = PageProcessor().decorate(
        {
            "vault_file": vault_file,
            "latest_job": latest_job,
        },
        request,
    )
    return render(request, "texplay/latex_play.html", context)


@login_required
@require_POST
def latex_compile(request, file_pk):
    vault_file = get_object_or_404(VaultFile, pk=file_pk)

    if vault_file.owner != request.user and not request.user.is_superuser:
        return JsonResponse({"ok": False, "error": "Not authorized"}, status=403)

    job = TexPlayJob.objects.create(vault_file=vault_file)

    dispatched = False
    try:
        from toto.celery_utils import celery_available
        if celery_available():
            from .tasks import compile_vault_latex_task
            compile_vault_latex_task.delay(job.pk)
            dispatched = True
    except Exception:
        pass

    if not dispatched:
        from .compile import compile_tex_file, save_compiled_pdf
        job.status = TexPlayJob.Status.RUNNING
        job.save(update_fields=["status"])
        try:
            pdf_bytes, log = compile_tex_file(vault_file)
            pdf_vf = save_compiled_pdf(vault_file, pdf_bytes)
            job.status = TexPlayJob.Status.SUCCESS
            job.log = log
            job.pdf_vault_file = pdf_vf
        except Exception as exc:
            job.status = TexPlayJob.Status.FAILED
            job.log = str(exc)
        finally:
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "log", "pdf_vault_file_id", "finished_at"])

    return JsonResponse({
        "ok": True,
        "job_pk": job.pk,
        "status": job.status,
        "finished": job.status in (TexPlayJob.Status.SUCCESS, TexPlayJob.Status.FAILED),
        "pdf_url": job.pdf_vault_file.get_public_url() if job.pdf_vault_file else "",
        "log": job.log,
    })


def latex_job_status(request, job_pk):
    job = get_object_or_404(TexPlayJob, pk=job_pk)
    return JsonResponse({
        "status": job.status,
        "log": job.log,
        "pdf_url": job.pdf_vault_file.get_public_url() if job.pdf_vault_file else "",
        "finished": job.status in (TexPlayJob.Status.SUCCESS, TexPlayJob.Status.FAILED),
    })
