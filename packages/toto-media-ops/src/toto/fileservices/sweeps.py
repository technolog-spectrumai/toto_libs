"""Fileservices' stuck-run sweep policy. Pure data — see toto.quota.sweeps.

``started_at`` here is auto_now_add — creation time, not actual start — so
the sweep honestly measures age-since-creation. Floor: the run_runtime dial
ceiling is 7200 s (hard limit 7300 on the extended path; the default path is
killed by the global 1800 s limit long before that); 10800 s is dead by
construction. The ("status", "started_at") index already serves this query.
"""

from toto.quota.sweeps import StuckRunPolicy, register

register(StuckRunPolicy(
    model_label="fileservices.FileServiceRun",
    active_values=("pending", "running"),
    closer="toto.fileservices.dispatch.fail_run",
    cutoff_seconds=10800,
    reference_fields=("started_at",),
    task_id_field="task_id",   # set on the bare-task path only
))
