"""The stuck-run policies for vault's queued jobs. Pure data — see
toto.quota.sweeps.

A celery hard time limit is a SIGKILL: no ``finally`` runs, so a killed
refresh leaves its row RUNNING and the poll waits forever. A refresh walks a
peer's listing page by page with a (5, 30) timeout per request; half an hour
is an order of magnitude beyond any legal walk, and a tighter cutoff means a
wedged row stops lying sooner.
"""

from toto.quota.sweeps import StuckRunPolicy, register

register(StuckRunPolicy(
    model_label="vault.BucketRefreshRun",
    active_values=("pending", "running"),
    closer="toto.vault.transfer_dispatch.fail_refresh_run",
    cutoff_seconds=1800,
    reference_fields=("started_at", "created_at"),
    status_field="status",
    task_id_field="task_id",
))
