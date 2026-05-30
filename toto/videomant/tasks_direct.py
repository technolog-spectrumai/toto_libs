"""Direct Celery task for UI-triggered (non-workflow) media jobs."""
import logging

from celery import shared_task

from .models import MediaJob
from .runner import MediaJobRunner

log = logging.getLogger(__name__)


@shared_task(bind=True, name="toto.videomant.tasks.run_direct", time_limit=7300, soft_time_limit=7200)
def run_direct_job(self, job_id: int) -> dict:
    job = MediaJob.objects.get(pk=job_id)
    runner = MediaJobRunner()
    try:
        runner.run(job)
    except Exception as exc:
        log.exception("Direct media job #%s failed: %s", job_id, exc)
        raise
    job.refresh_from_db()
    return {"job_id": job_id, "status": job.status, "progress_percent": job.progress_percent}
