"""How long one page may take.

Declared here rather than as a Celery constant so the ceiling can be reasoned
about next to the numbers it must respect: the platform's soft limit is 1500 s
and Redis redelivers anything still running after 2100 s, so a per-page budget
has to stay far below both. It does — a 300 DPI A4 page is a second or two of
rasterising plus a few of reading.
"""

from __future__ import annotations

#: Seconds. Generous against a slow, noisy scan; small enough that one
#: pathological page cannot hold a worker slot while a whole book waits.
DEFAULT_PAGE_SECONDS = 240


def page_budget(user=None) -> int:
    """The per-page soft limit, in seconds.

    Asks toto.quota.times when that dial exists, so an operator can raise it
    the way manta's and fileservices' runtimes are raised, and falls back to the
    declared default otherwise. Never raises.
    """
    try:
        from toto.quota import times

        seconds = times.effective_seconds("ocr.page_runtime", user=user)
        if seconds:
            return int(seconds)
    except Exception:  # noqa: BLE001 — the declared default is the safe answer
        pass
    return DEFAULT_PAGE_SECONDS
