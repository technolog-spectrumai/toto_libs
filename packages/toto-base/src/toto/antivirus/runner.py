"""The worker-side seam between the task and the service.

Thin on purpose, like texlab's: the predefined task hands over a pk, this
loads the row and calls the one function that does the work.
"""

from __future__ import annotations

from .models import ScanRun
from .services import execute


def execute_run(run_id: int) -> ScanRun:
    run = ScanRun.objects.select_related("file").get(pk=run_id)
    return execute(run)
