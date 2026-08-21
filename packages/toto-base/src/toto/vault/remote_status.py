"""Reading a remote bucket's health from what jobs have already stamped.

**No function here ever touches the network.** That is the rule the whole
remote surface rests on (``mirror.py``: *"every page render is a plain DB query
and no request ever probes a peer"*), and it is what lets a listing render
instantly and a stale answer be an honest one rather than a hang.

Lifted out of ``BucketMetricsView._remote_info`` so the metrics page, the
Remote Buckets listing and the per-bucket detail all read the same columns and
cannot drift into three different opinions about whether a peer is up.
"""

from __future__ import annotations

from .models import StorageBackend


def peer_info(bucket):
    """The Remote card's payload, from stamps only — never a probe.

    ``reachability`` is tri-state on purpose: "never checked" is a true answer,
    and a green badge nobody earned would be a lie (the antivirus tooltip
    doctrine).

    Returns ``None`` for anything that is not a mounted bucket — callers rely
    on that to decide whether to render the card at all, so widening it to S3
    would newly render the Remote card on an S3 metrics page.
    """
    if bucket.storage_backend != StorageBackend.REMOTE_TOTO:
        return None
    peer = bucket.peer if bucket.peer_id else None
    if peer is None:
        return {"peer": None}
    if peer.last_error:
        reachability = "down"
    elif peer.last_ok_at:
        reachability = "ok"
    else:
        reachability = "unknown"
    return {
        "peer": peer,
        "reachability": reachability,
        "last_refreshed_at": bucket.last_refreshed_at,
    }
