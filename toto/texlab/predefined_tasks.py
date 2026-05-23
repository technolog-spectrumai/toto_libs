from django.utils import timezone

from toto.workflows.predefined_tasks import register


@register("texlab_compile_latex")
def texlab_compile_latex(input_data: dict) -> dict:
    from .compile import compile_tex_to_pdf
    from .models import CompileRun, LatexFile

    file_id = (input_data.get("data") or {}).get("file_id")
    run_id = (input_data.get("data") or {}).get("run_id")

    if file_id is None or run_id is None:
        raise ValueError("texlab_compile_latex requires file_id and run_id in input data.")

    lf = LatexFile.objects.select_related("vault_file", "workspace__bucket").get(id=file_id)
    run = CompileRun.objects.get(id=run_id)
    run.status = CompileRun.RUNNING
    run.save(update_fields=["status"])

    try:
        pdf_vault, log = compile_tex_to_pdf(lf.vault_file, lf.workspace)
        run.status = CompileRun.SUCCESS
        run.log = log
        run.pdf_url = pdf_vault.get_public_url()
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "log", "pdf_url", "finished_at"])
        return {"data": {"run_id": run_id, "pdf_url": run.pdf_url, "status": "success"}}
    except Exception as exc:
        run.status = CompileRun.FAILED
        run.log = str(exc)
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "log", "finished_at"])
        raise
