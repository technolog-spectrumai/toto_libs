from django.utils import timezone

from toto.workflows.predefined_tasks import register


@register("texplay_compile")
def texplay_compile(input_data: dict) -> dict:
    """
    Workflow predefined task: compile a vault .tex file.
    Expected input_data: {"data": {"job_pk": <int>}}
    """
    from toto.texplay.compile import compile_tex_file, save_compiled_pdf
    from toto.texplay.models import TexPlayJob

    job_pk = (input_data.get("data") or {}).get("job_pk")
    job = TexPlayJob.objects.select_related("vault_file__bucket").get(pk=job_pk)
    job.status = TexPlayJob.Status.RUNNING
    job.save(update_fields=["status"])

    try:
        pdf_bytes, log = compile_tex_file(job.vault_file)
        pdf_vf = save_compiled_pdf(job.vault_file, pdf_bytes)
        job.status = TexPlayJob.Status.SUCCESS
        job.log = log
        job.pdf_vault_file = pdf_vf
    except Exception as exc:
        job.status = TexPlayJob.Status.FAILED
        job.log = str(exc)
        raise
    finally:
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "log", "pdf_vault_file_id", "finished_at"])

    return {"data": {"job_pk": job_pk, "status": job.status, "pdf_url": job.pdf_vault_file.get_public_url() if job.pdf_vault_file else ""}}
