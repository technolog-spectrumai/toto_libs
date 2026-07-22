from django.utils import timezone

from toto.workflows.predefined_tasks import register


@register("antaresia_run_python")
def antaresia_run_python(input_data: dict) -> dict:
    from toto.antaresia.models import PythonRun
    from toto.antaresia.run import run_python_script
    from toto.vault.models import VaultFile

    data = input_data.get("data") or {}
    vault_file_pk = data.get("vault_file_pk")
    run_id = data.get("run_id")

    if vault_file_pk is None or run_id is None:
        raise ValueError("antaresia_run_python requires vault_file_pk and run_id in input data.")

    vault_file = VaultFile.objects.select_related("bucket", "directory").get(pk=vault_file_pk)
    run = PythonRun.objects.get(id=run_id)
    run.status = PythonRun.RUNNING
    run.save(update_fields=["status"])

    try:
        stdout, stderr, exit_code = run_python_script(vault_file)
        run.stdout = stdout
        run.stderr = stderr
        run.exit_code = exit_code
        run.status = PythonRun.SUCCESS if exit_code == 0 else PythonRun.FAILED
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "stdout", "stderr", "exit_code", "finished_at"])
        return {"data": {"run_id": run_id, "exit_code": exit_code, "status": run.status}}
    except Exception as exc:
        run.status = PythonRun.FAILED
        run.stderr = str(exc)
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "stderr", "finished_at"])
        raise
