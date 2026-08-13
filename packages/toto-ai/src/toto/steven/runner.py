"""The worker side: one run, start to finish.

Thin on purpose — `services.execute` holds every decision, so the same code path
runs under a worker, under a test, and under `manage.py shell`. This module only
loads the row and translates an unexpected explosion into a closed run, because a
row stuck at RUNNING forever is the failure mode the sweeper exists to clean up
and the one users actually notice.
"""

from __future__ import annotations

import logging

from .models import AiRun, RunStatus

logger = logging.getLogger(__name__)


def execute_run(run_id: int) -> AiRun:
    run = AiRun.objects.filter(pk=run_id).first()
    if run is None:
        raise ValueError(f"AiRun {run_id} does not exist.")
    if run.is_finished:
        return run

    from . import services

    try:
        return services.execute(run)
    except Exception as exc:  # noqa: BLE001 - a crash must still close the row
        logger.exception("steven: run %s crashed", run_id)
        run.finish(status=RunStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
        return run
