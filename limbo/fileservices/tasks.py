from celery import shared_task


@shared_task(bind=True)
def run_file_service_task(self, run_id: int) -> bool:
    from .models import FileServiceRun
    from .runner import execute_run

    FileServiceRun.objects.filter(pk=run_id).update(task_id=self.request.id or "")
    try:
        execute_run(run_id)
        return True
    except Exception:
        return False
