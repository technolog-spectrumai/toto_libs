from celery import shared_task
from django.utils import timezone


@shared_task(bind=True)
def compile_vault_latex_task(self, job_pk: int) -> dict:
    from toto.texplay.compile import compile_tex_file, save_compiled_pdf
    from toto.texplay.models import TexPlayJob

    job = TexPlayJob.objects.select_related("vault_file__bucket").get(pk=job_pk)
    job.status = TexPlayJob.Status.RUNNING
    job.celery_task_id = self.request.id or ""
    job.save(update_fields=["status", "celery_task_id"])

    try:
        pdf_bytes, log = compile_tex_file(job.vault_file)
        pdf_vf = save_compiled_pdf(job.vault_file, pdf_bytes)
        job.status = TexPlayJob.Status.SUCCESS
        job.log = log
        job.pdf_vault_file = pdf_vf
    except Exception as exc:
        job.status = TexPlayJob.Status.FAILED
        job.log = str(exc)
    finally:
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "log", "pdf_vault_file_id", "celery_task_id", "finished_at"])

    return {"job_pk": job_pk, "status": job.status}
