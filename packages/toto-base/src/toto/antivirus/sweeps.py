"""The stuck-run policy. Pure data — see toto.quota.sweeps.

A celery hard time limit is a SIGKILL: no ``finally`` runs, so a killed scan
leaves its row RUNNING and the modal's progress bar waits forever. The cutoff
is 900 seconds — a scan is a bounded pass over bounded bytes and finishes in
milliseconds, so fifteen minutes is an order of magnitude beyond any legal
run, and a tighter cutoff means a wedged row stops lying sooner.
"""

from toto.quota.sweeps import StuckRunPolicy, register

register(StuckRunPolicy(
    model_label="antivirus.ScanRun",
    active_values=("pending", "running"),
    closer="toto.antivirus.dispatch.fail_run",
    cutoff_seconds=900,
    reference_fields=("created_at",),
    status_field="status",
    task_id_field="task_id",
))
