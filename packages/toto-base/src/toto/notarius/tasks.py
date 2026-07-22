from celery import shared_task
from django.utils import timezone


@shared_task(bind=True, name="toto.notarius.tasks.render_contract_pdf_task")
def render_contract_pdf_task(self, job_pk: int) -> dict:
    """Render a ``.contract`` to PDF via its type's LaTeX template (pdflatex) and
    save the result as a sibling VaultFile. Updates the ContractPdfJob status."""
    from toto.notarius import latex
    from toto.notarius.models import ContractPdfJob

    job = ContractPdfJob.objects.select_related("vault_file__bucket").get(pk=job_pk)
    job.status = ContractPdfJob.Status.RUNNING
    job.celery_task_id = self.request.id or ""
    job.save(update_fields=["status", "celery_task_id"])

    try:
        pdf_bytes, log = latex.contract_to_pdf(job.vault_file)
        pdf_vf = latex.save_contract_pdf(job.vault_file, pdf_bytes)
        job.status = ContractPdfJob.Status.SUCCESS
        job.log = log[-5000:]
        job.pdf_vault_file = pdf_vf
    except Exception as exc:
        job.status = ContractPdfJob.Status.FAILED
        job.log = str(exc)
    finally:
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "log", "pdf_vault_file_id", "celery_task_id", "finished_at"])

    return {"job_pk": job_pk, "status": job.status}
