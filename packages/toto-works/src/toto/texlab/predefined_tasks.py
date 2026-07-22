from django.utils import timezone

from toto.workflows.predefined_tasks import register


@register("texlab_compile_latex")
def texlab_compile_latex(input_data: dict) -> dict:
    from toto.texlab.compile import compile_tex_to_pdf
    from toto.texlab.models import CompileRun
    from toto.vault.models import VaultFile

    data = input_data.get("data") or {}
    vault_file_pk = data.get("vault_file_pk")
    run_id = data.get("run_id")

    if vault_file_pk is None or run_id is None:
        raise ValueError("texlab_compile_latex requires vault_file_pk and run_id in input data.")

    vault_file = VaultFile.objects.select_related("bucket", "directory").get(pk=vault_file_pk)
    run = CompileRun.objects.get(id=run_id)
    run.status = CompileRun.RUNNING
    run.save(update_fields=["status"])

    try:
        pdf_vault, log = compile_tex_to_pdf(vault_file)
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
