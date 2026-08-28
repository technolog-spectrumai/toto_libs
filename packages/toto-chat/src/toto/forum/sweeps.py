"""Cleanup runs that stopped without saying so.

Declared for the platform's one stuck-run sweeper (`toto.quota.sweeps`,
collected by `autodiscover_plugins("sweeps")` and swept hourly) rather than
hand-rolled here. The model label and the closer are STRINGS, resolved at sweep
time, so this file adds no import edge at web boot and is inert on a host that
does not run the sweeper.

It matters more than a tidy status column: `cleanup.in_flight()` refuses a
second sweep while a row says RUNNING, so a worker killed mid-run would block
every future cleanup until something closed its row. This is that something.
"""

from toto.quota.sweeps import StuckRunPolicy, register

#: Six hours: far beyond the task's own 30-minute hard limit plus any queue
#: wait, so a row this old is a lie rather than a slow night.
register(StuckRunPolicy(
    model_label="forum.ForumCleanupRun",
    active_values=("pending", "running"),
    closer="toto.forum.cleanup.fail_run",
    cutoff_seconds=21600,
    reference_fields=("started_at",),
    status_field="status",
))
