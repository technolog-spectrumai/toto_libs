"""The repo app's stuck-run sweep policy. Pure data — see toto.quota.sweeps.

Floor: git ops run under the global 1800 s hard limit with 120–300 s
subprocess timeouts; 7200 s is dead by construction.
"""

from toto.quota.sweeps import StuckRunPolicy, register

register(StuckRunPolicy(
    model_label="repo.GitRun",
    active_values=("pending", "running"),
    closer="toto.repo.runner.fail_run",
    cutoff_seconds=7200,
    reference_fields=("started_at", "created_at"),
    task_id_field="task_id",   # set on the bare-task fallback path only
))
