"""Anastasia's stuck-execution policy. Pure data — see toto.quota.sweeps.

Two ways an execution can be stranded RUNNING, and this closes both:

* the celery worker holding it was SIGKILLed (a hard time limit runs no
  ``finally``), so nothing ever reported the result;
* the manager died mid-job, so the container is gone and no reconciliation
  reached this row.

The cutoff is DERIVED, not chosen. The longest legal execution is the largest
``max_timeout`` in the operation catalogue — 7200s for ``normalize_media`` —
so no legal run can still be alive after that. 10800 gives an hour and a half
of margin for a manager that is slow to reconcile rather than dead. A row older
than this is dead by construction, which is why the sweeper needs to know
nothing about what the job was doing.

Note there is no ``task_id_field``: an Execution is not a celery task. The
caller's own run row carries the task id and its own policy revokes it; this
row's container is the manager's to reap.
"""

from toto.quota.sweeps import StuckRunPolicy, register

register(StuckRunPolicy(
    model_label="anastasia.Execution",
    active_values=("pending", "running"),
    closer="toto.anastasia.execute.close_stuck",
    cutoff_seconds=10800,
    reference_fields=("started_at", "created_at"),
    status_field="status",
))
