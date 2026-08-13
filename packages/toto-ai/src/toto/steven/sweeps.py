"""Steven's stuck-run policy. Pure data — see toto.quota.sweeps.

A celery hard time limit is a SIGKILL: no `finally` runs, so a killed request
leaves its row RUNNING and the browser's spinner turns forever. This is what
closes those rows.

The cutoff is DERIVED, not chosen. `AiProvider.timeout` is 60 seconds by default
and a generous operator might raise it to a few minutes; 3600 is an order of
magnitude beyond any legal call, so a row older than this is dead by
construction. Shorter than aralia's 7200 because a completion is not a render —
nothing here legitimately takes an hour, and a tighter cutoff means a wedged row
stops lying to its owner sooner.
"""

from toto.quota.sweeps import StuckRunPolicy, register

register(StuckRunPolicy(
    model_label="steven.AiRun",
    active_values=("pending", "running"),
    closer="toto.steven.dispatch.fail_run",
    cutoff_seconds=3600,
    reference_fields=("created_at",),
    status_field="status",
    task_id_field="task_id",
))
