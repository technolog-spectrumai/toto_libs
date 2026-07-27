from celery import shared_task
from django.utils import timezone


@shared_task(bind=True)
def run_python_task(self, vault_file_pk: int, run_id: int) -> bool:
    from toto.antaresia.models import PythonRun
    from toto.antaresia.run import run_python_script
    from toto.vault.models import VaultFile

    run = PythonRun.objects.get(id=run_id)
    run.status = PythonRun.RUNNING
    run.task_id = self.request.id or ""
    run.save(update_fields=["status", "task_id"])

    try:
        vault_file = VaultFile.objects.select_related("bucket", "directory").get(pk=vault_file_pk)
        stdout, stderr, exit_code = run_python_script(vault_file)
        run.stdout = stdout
        run.stderr = stderr
        run.exit_code = exit_code
        run.status = PythonRun.SUCCESS if exit_code == 0 else PythonRun.FAILED
        return exit_code == 0
    except Exception as exc:
        run.status = PythonRun.FAILED
        run.stderr = str(exc)
        return False
    finally:
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "task_id", "stdout", "stderr", "exit_code", "finished_at"])
