from celery import shared_task
from django.utils import timezone


@shared_task(bind=True)
def compile_latex_task(self, file_id: int, run_id: int) -> bool:
    from toto.texlab.compile import compile_tex_to_pdf
    from toto.texlab.models import CompileRun, LatexFile

    run = CompileRun.objects.get(id=run_id)
    run.status = CompileRun.RUNNING
    run.task_id = self.request.id or ""
    run.save(update_fields=["status", "task_id"])

    try:
        lf = LatexFile.objects.select_related(
            "vault_file", "workspace__bucket"
        ).get(id=file_id)
        pdf_vault, log = compile_tex_to_pdf(lf.vault_file, lf.workspace)
        run.status = CompileRun.SUCCESS
        run.log = log
        run.pdf_url = pdf_vault.get_public_url()
        return True
    except Exception as exc:
        run.status = CompileRun.FAILED
        run.log = str(exc)
        return False
    finally:
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "task_id", "log", "pdf_url", "finished_at"])
