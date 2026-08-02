"""Delivery: push queued outbox rows to the peer, relay its answers back in.

The peer's reply to a delivered message is itself a signed envelope (a fulfill
receipt or a reject), and it is fed straight through OUR inbox — same
verification, same replay shield — so the fast response path and the peer's own
durable redelivery converge on exactly one application.

``http_post`` is module-level on purpose: the in-process two-platform harness
patches it, the way the identity layer's loopback does.
"""

from __future__ import annotations

import base64
import json

from django.db import transaction

from ..models import ClearingOutbox, LedgerPeer
from . import bridge, transfers

BACKCHANNEL_TIMEOUT = (5, 30)
MAX_ATTEMPTS = 5


def http_post(url: str, *, auth: tuple[str, str], payload: dict) -> tuple[int, dict]:
    """POST a JSON envelope with HTTP Basic. Returns (status, parsed body)."""
    import requests

    token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
    response = requests.post(
        url, data=json.dumps(payload), timeout=BACKCHANNEL_TIMEOUT,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Basic {token}"},
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, body


def deliver(row: ClearingOutbox) -> bool:
    """Send one message. Returns True when the peer acknowledged it."""
    peer = row.peer
    claimed = ClearingOutbox.objects.filter(
        pk=row.pk, state__in=[ClearingOutbox.QUEUED, ClearingOutbox.FAILED]
    ).update(state=ClearingOutbox.SENDING)
    if not claimed:
        return row.state == ClearingOutbox.ACKED

    url = peer.base_url.rstrip("/") + "/clearing/api/inbox/"
    try:
        status, body = http_post(url, auth=(peer.client_id, peer.get_secret()),
                                 payload=row.envelope())
    except Exception as exc:  # transport failure — keep the error verbatim
        _fail(row, f"{type(exc).__name__}: {exc}")
        return False

    if status != 200:
        _fail(row, f"HTTP {status}: {json.dumps(body)[:400]}")
        return False

    ClearingOutbox.objects.filter(pk=row.pk).update(
        state=ClearingOutbox.ACKED, last_error="")

    # The peer may answer with a signed envelope of its own (a receipt). Relay
    # it through our inbox so it is verified and applied exactly once.
    reply = body.get("reply")
    if isinstance(reply, dict) and reply.get("kind"):
        try:
            bridge.receive(peer, reply, transfers.dispatch)
        except bridge.InboxRefusal:
            pass
    return True


def _fail(row: ClearingOutbox, error: str) -> None:
    attempts = row.attempts + 1
    # No silent auto-retry: past the cap the row is HELD for a human, with the
    # peer's own words attached.
    state = ClearingOutbox.HELD if attempts >= MAX_ATTEMPTS else ClearingOutbox.FAILED
    ClearingOutbox.objects.filter(pk=row.pk).update(
        state=state, attempts=attempts, last_error=error[:2000])


def dispatch_queued(limit: int = 50) -> int:
    """Deliver queued messages in sequence order. Returns the count acked.

    Ordering matters: the receiver refuses a gap, so a later message must not
    overtake an earlier one on the same link.
    """
    sent = 0
    for peer in LedgerPeer.objects.filter(status=LedgerPeer.STATUS_ACTIVE):
        rows = (ClearingOutbox.objects
                .filter(peer=peer, state=ClearingOutbox.QUEUED)
                .order_by("seq")[:limit])
        for row in rows:
            if not deliver(row):
                break  # keep the stream in order; the sweep retries later
            sent += 1
    return sent


def redrive(limit: int = 50) -> int:
    """Re-queue FAILED rows below the attempt cap (HELD ones wait for a human)."""
    return (ClearingOutbox.objects
            .filter(state=ClearingOutbox.FAILED, attempts__lt=MAX_ATTEMPTS)
            .order_by("seq")[:limit]
            .update(state=ClearingOutbox.QUEUED))


def flush_after_commit() -> None:
    """Push as soon as the current transaction commits.

    Celery-less hosts (a lean child) still get prompt delivery; where a worker
    exists the beat sweep is the safety net for whatever this misses.
    """
    transaction.on_commit(lambda: dispatch_queued())
