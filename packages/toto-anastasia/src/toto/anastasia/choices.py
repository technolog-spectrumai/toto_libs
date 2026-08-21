"""The vocabularies, as pure data.

This module is imported by ``metrics.py``/``sweeps.py``-style declarations and
by the Django-free manager, so it must never import Django models, settings, or
anything that touches a database.
"""

from __future__ import annotations

#: What a mounted Gear can be doing. Derived from samples, never stored as the
#: authority — see ``services.derive_state``; the manager's cgroup reading is
#: the truth and this is the cached answer with a timestamp beside it.
READY = "ready"
BUSY = "busy"
DEGRADED = "degraded"
DEAD = "dead"
UNMOUNTED = "unmounted"

#: The states in which a Gear will accept a new execution. DEGRADED is
#: deliberately absent: a Gear that has already OOM-killed something is not a
#: Gear to hand more work to, and the user can see why on the page.
ACCEPTING = frozenset({READY, BUSY})

#: A Gear in one of these has runtime worth reconciling against Docker.
MOUNTED = frozenset({READY, BUSY, DEGRADED})

GEAR_STATES = (
    (READY, "Ready"),
    (BUSY, "Busy"),
    (DEGRADED, "Degraded"),
    (DEAD, "Dead"),
    (UNMOUNTED, "Unmounted"),
)

#: An execution's life. PENDING means the row exists and the manager has not
#: been asked yet — the same "a row the browser can poll before dispatch could
#: fail" reasoning as aralia.AraliaRun.
PENDING = "pending"
RUNNING = "running"
SUCCESS = "success"
FAILED = "failed"
KILLED = "killed"
LOST = "lost"

EXECUTION_STATUSES = (
    (PENDING, "Pending"),
    (RUNNING, "Running"),
    (SUCCESS, "Success"),
    (FAILED, "Failed"),
    (KILLED, "Killed"),
    (LOST, "Lost"),
)

#: Terminal statuses. A redelivered task must not resurrect one of these — the
#: guard every runner in this codebase opens with.
FINISHED = frozenset({SUCCESS, FAILED, KILLED, LOST})
