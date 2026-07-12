from celery import shared_task


@shared_task(bind=True)
def run_git_task(self, run_id: int) -> bool:
    from .models import GitRun
    from .runner import execute_git_run

    GitRun.objects.filter(pk=run_id).update(task_id=self.request.id or "")
    try:
        execute_git_run(run_id)
        return True
    except Exception:
        return False
