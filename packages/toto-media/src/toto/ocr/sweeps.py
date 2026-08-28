"""A run whose worker was killed must not say RUNNING forever.

`QuotaConfig.ready()` autodiscovers `<app>/sweeps.py`, and the STUCK_RUN_SWEEP
beat entry already exists — so this costs one file and no wiring.

A hard SIGKILL runs no `finally`: the page row stays RUNNING, the run never
settles, and the owner's "one job at a time" rule then locks them out of the
feature permanently. That is the failure this closes.
"""

from toto.quota.sweeps import StuckRunPolicy, register

register(StuckRunPolicy(
    model_label="ocr.OcrRun",
    active_values=("pending", "running"),
    closer="toto.ocr.runs.fail_run",
    # Comfortably longer than the longest legitimate run: 400 pages at the
    # per-page ceiling, plus room. Anything older than this is not slow, it is
    # a lie left behind by a killed worker.
    cutoff_seconds=6 * 60 * 60,
))
