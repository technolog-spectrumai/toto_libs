from __future__ import annotations

from celery import shared_task

from .services import download_model_weights, run_transcription_job as run_job


@shared_task(bind=True)
def run_transcription_job(self, job_id: int):
    return run_job(job_id)


@shared_task(bind=True, time_limit=7200, soft_time_limit=6900)
def download_whisper_model(self, config_pk: int):
    """Download model weights for a WhisperModelConfig row."""
    download_model_weights(config_pk)
