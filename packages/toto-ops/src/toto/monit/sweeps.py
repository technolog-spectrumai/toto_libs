"""Run records that never said how they ended (2026-10-01).

Declared for the platform's one stuck-run sweeper (`toto.quota.sweeps`,
collected by `autodiscover_plugins("sweeps")`, swept hourly). A child killed
by the hard time limit, or lost, is closed by the worker process's own
`task_failure` (toto.monit.heartbeats); a worker CONTAINER stopped mid-run —
a deploy, the out-of-memory killer — sends nothing at all, and its run would
read "running" on the Jobs page for ever. The heartbeat itself is unharmed
either way: it counts the start.

Six hours: far beyond the longest scheduled task's hard limit (the levy's
hour) plus any queue wait, so a row this old is a lie rather than a slow
night. Nothing to revoke — the task is long dead.
"""

from toto.quota.sweeps import StuckRunPolicy, register

register(StuckRunPolicy(
    model_label="monit.TaskRun",
    active_values=("running",),
    closer="toto.monit.heartbeats.close_stuck",
    cutoff_seconds=21600,
    reference_fields=("started_at",),
))
