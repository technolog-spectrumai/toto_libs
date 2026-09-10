"""Whether this machine can take another Capsule right now.

The pool ceiling is a promise about capacity the deployment CHOSE to hand out.
Pressure is about the machine underneath it, which may be under load the pool
knows nothing about — a sibling container, a backup run, an operator's build.

The rule, and it is deliberate: **pressure refuses new mounts and never kills a
mounted Capsule.** A user who reserved capacity and mounted it has been promised
something; taking it back to serve someone else would make a reservation
meaningless. So pressure closes the door and leaves the room alone — the same
judgement the platform's levy engine makes when it refuses new usage rather
than deleting what exists.

Reads ``/proc`` and cgroup files directly, the way ``toto.monit.collectors``
does. No psutil: the suite pins none, and this is four files.

Django-free.
"""

from __future__ import annotations

import logging
import os
import shutil

log = logging.getLogger("toto.anastasia.executor.pressure")

#: Refuse new mounts below this much free RAM. Not a fraction: a percentage of
#: a big machine is a lot of absolute memory, and what actually matters is
#: whether the next runner can start.
DEFAULT_MIN_FREE_MB = 512
DEFAULT_MIN_FREE_DISK_MB = 2048

#: The kernel's own stall accounting, if present. some/avg10 is the share of
#: the last ten seconds in which at least one task was stalled on memory.
#: Above this the machine is already thrashing, whatever "free" says.
DEFAULT_MAX_MEMORY_STALL = 20.0


def meminfo_free_mb() -> int | None:
    """MemAvailable, which is what can actually be handed out — not MemFree."""
    try:
        with open("/proc/meminfo") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def memory_stall_percent(path: str = "/proc/pressure/memory") -> float | None:
    """PSI: how much of the recent past was spent stalled on memory."""
    try:
        with open(path) as handle:
            for line in handle:
                if line.startswith("some"):
                    for field in line.split():
                        key, _, value = field.partition("=")
                        if key == "avg10":
                            return float(value)
    except (OSError, ValueError):
        return None
    return None


def free_disk_mb(path: str) -> int | None:
    try:
        return shutil.disk_usage(path).free // (1024 * 1024)
    except OSError:
        return None


def report(staging_root: str, *, min_free_mb: int = DEFAULT_MIN_FREE_MB,
           min_free_disk_mb: int = DEFAULT_MIN_FREE_DISK_MB,
           max_stall: float = DEFAULT_MAX_MEMORY_STALL) -> dict:
    """Whether new mounts are welcome, and the numbers behind the answer.

    A reading that could not be taken is NOT treated as healthy: an unreadable
    /proc means we do not know, and refusing to admit on "I do not know" is the
    same rule lifecycle's work probes use — a probe that cannot answer blocks,
    it does not wave through.
    """
    free_mb = meminfo_free_mb()
    stall = memory_stall_percent()
    disk_mb = free_disk_mb(staging_root if os.path.isdir(staging_root) else "/")

    reasons = []
    if free_mb is None:
        reasons.append("free memory could not be read")
    elif free_mb < min_free_mb:
        reasons.append(f"only {free_mb} MB of memory available")

    if disk_mb is None:
        reasons.append("free staging disk could not be read")
    elif disk_mb < min_free_disk_mb:
        reasons.append(f"only {disk_mb} MB of staging disk left")

    # A missing PSI file is not a refusal: PSI is a kernel build option, and
    # plenty of perfectly healthy hosts do not have it. Unlike the two above,
    # its ABSENCE is expected rather than suspicious.
    if stall is not None and stall > max_stall:
        reasons.append(f"the machine is stalled on memory {stall:.0f}% of the time")

    return {
        "admitting": not reasons,
        "reasons": reasons,
        "free_mb": free_mb,
        "free_disk_mb": disk_mb,
        "memory_stall": stall,
    }


def refusal(report_data: dict) -> str:
    """One sentence for a user who just tried to mount."""
    reasons = report_data.get("reasons") or ["this machine is under pressure"]
    return ("This machine cannot take another mounted Capsule right now: "
            + "; ".join(reasons) + ". Your reservation is untouched — try "
            "mounting again shortly.")
