from __future__ import annotations

from celery import shared_task

from .services import run_transcription_job as run_job


@shared_task(bind=True)
def run_transcription_job(self, job_id: int):
    return run_job(job_id)
