"""Direct Celery task — delegates execution to the command class."""
import logging

from celery import shared_task

from .commands import get_command
from .models import FileJob

log = logging.getLogger(__name__)


@shared_task(bind=True, name="toto.manta.tasks.run_direct", time_limit=7300, soft_time_limit=7200)
def run_direct_job(self, job_id: int) -> dict:
    # Worker-side capture (belt to the dispatch-side one): covers redelivery
    # and any send_task caller that skipped apply_async.
    FileJob.objects.filter(pk=job_id).update(celery_task_id=self.request.id or "")
    job = FileJob.objects.get(pk=job_id)
    try:
        get_command(job.command)().execute(job)
    except Exception as exc:
        log.exception("Manta job #%s failed: %s", job_id, exc)
        raise
    job.refresh_from_db()
    return {"job_id": job_id, "status": job.status}
