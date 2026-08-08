"""Manta's stuck-run sweep policy. Pure data — see toto.quota.sweeps.

Floor: the job_runtime dial ceiling is 14400 s and the dispatch hard limit is
budget + 100; 18000 s (5 h) is dead by construction whatever the grant.
"""

from toto.quota.sweeps import StuckRunPolicy, register

register(StuckRunPolicy(
    model_label="manta.FileJob",
    active_values=("pending", "running"),
    closer="toto.manta.models.fail_job",
    cutoff_seconds=18000,
    reference_fields=("started_at", "created_at"),
    task_id_field="celery_task_id",
))
