"""Decide what to do with one incoming row. Pure: no database, no network, no writes.

This is the module that makes "newest change wins" implementable on a scope where most
models carry no modification timestamp. A timestamp comparison answers *"which edit is
newer?"* and cannot be asked here. The merge base answers a different question that can:
**who edited this row since the two instances last agreed?**

The base records two checksums, and the pair is essential rather than tidy:

* ``peer_checksum`` — what the peer's payload hashed to when we accepted it;
* ``local_checksum`` — what the local row hashed to, projected through the same policy,
  immediately after it was written.

They differ legitimately whenever ``save()`` transforms a row on arrival:
``locations.Address`` re-derives latitude/longitude from geometry, and several models
generate slugs. With a single checksum the *next* run would read every row of those
models as locally edited — a conflict storm across a third of the replicated scope,
on a healthy link. ``test_second_run_is_all_skips`` is the guard.

Two outcomes the obvious design omits, and both matter:

* **``deleted_locally``.** With a base, "absent locally" is *evidence of a local delete*,
  not an absence. Silently re-inserting is precisely the silent degradation this feature
  refuses; it is reported, and the operator may choose to resurrect.
* **``absent_on_peer``.** datalink never deletes. A stage that half-ran and deleted would
  destroy real data, and nothing in the requirement asked for it. It is counted so the
  divergence is visible, and acted on never.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

# --- what to do with the row ----------------------------------------------
INSERT = "insert"      # nothing local; create it and record a base
UPDATE = "update"      # the peer changed it and we did not
SKIP = "skip"          # nothing to do
ADOPT = "adopt"        # identical already; record a base so future runs can tell
CONFLICT = "conflict"  # the engine will not decide this on its own

# --- why, for the counters and the report ---------------------------------
REASON_NEW = "new"
REASON_UNCHANGED = "unchanged"
REASON_IDENTICAL_NO_BASE = "identical_no_base"
REASON_PEER_CHANGED = "peer_changed"
REASON_LOCAL_AHEAD = "local_ahead"
REASON_TIMESTAMP = "timestamp"

# --- conflict kinds (mirrored on DatalinkConflict) -------------------------
BOTH_CHANGED = "both_changed"
NO_BASE_DIVERGENCE = "no_base_divergence"
DELETED_LOCALLY = "deleted_locally"


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str = ""
    conflict_kind: str = ""
    # Only set when a timestamp actually arbitrated, so an auto-resolution is always
    # visible in the report rather than silently applied.
    auto_resolved: bool = False
    detail: str = ""
    field_diff: Mapping[str, Any] = field(default_factory=dict)


def normalize(value: Any) -> Any:
    """Compare values the way the checksum does.

    Lifted from ``sql_neo4j_sync.planner.normalize``'s role: the point is that a diff
    and a checksum must never disagree about whether two values are the same.
    """
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return [normalize(v) for v in value]
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in sorted(value.items())}
    return value


def field_diff(local_fields: Mapping[str, Any], peer_fields: Mapping[str, Any]) -> dict:
    """Per-field ``{"local": ..., "peer": ...}`` for the operator's decision.

    Adapted from ``sql_neo4j_sync.planner.prop_changes``, with its ``from``/``to`` keys
    renamed to say which side each value came from — a reviewer reading a conflict needs
    to know whose value is whose, not which direction a projection was running.

    Every key present on either side is considered, so a field the peer dropped shows up
    rather than being invisible.
    """
    changes: dict[str, dict[str, Any]] = {}
    for key in sorted(set(local_fields) | set(peer_fields)):
        local = normalize(local_fields.get(key))
        peer = normalize(peer_fields.get(key))
        if local != peer:
            changes[key] = {"local": local, "peer": peer}
    return changes


def decide(
    *,
    local_present: bool,
    base: Any | None,
    peer_checksum: str,
    local_checksum: str | None,
    peer_changed_at=None,
    local_changed_at=None,
    has_timestamp: bool = False,
    allow_timestamp_tiebreak: bool = True,
    local_fields: Mapping[str, Any] | None = None,
    peer_fields: Mapping[str, Any] | None = None,
) -> Decision:
    """The whole decision table, in one place.

    ``base`` is a ``DatalinkMergeBase`` or None; only its two checksum attributes are
    read, so tests can pass any object with them.
    """
    if base is None:
        if not local_present:
            return Decision(INSERT, reason=REASON_NEW)
        # Never synced, and something is already here under the same identity. If it is
        # byte-identical there is nothing to argue about — record a base so that from
        # now on the engine can tell an edit from a coincidence.
        if local_checksum == peer_checksum:
            return Decision(ADOPT, reason=REASON_IDENTICAL_NO_BASE)
        return _conflict(
            NO_BASE_DIVERGENCE,
            "The two instances have never synced this row and it differs. Nothing is "
            "known about who changed what, so this is yours to decide.",
            local_fields, peer_fields,
        )

    if not local_present:
        # A base plus no local row is evidence of a local DELETE, not an absence.
        # Re-inserting would silently undo it.
        return _conflict(
            DELETED_LOCALLY,
            "This row was deleted here since the last sync, and the peer still has it. "
            "datalink will not resurrect it on its own.",
            local_fields, peer_fields,
        )

    peer_changed = peer_checksum != getattr(base, "peer_checksum", None)
    local_changed = local_checksum != getattr(base, "local_checksum", None)

    if not peer_changed and not local_changed:
        return Decision(SKIP, reason=REASON_UNCHANGED)

    if peer_changed and not local_changed:
        return Decision(UPDATE, reason=REASON_PEER_CHANGED)

    if local_changed and not peer_changed:
        # Ours is ahead. Not a conflict — there is nothing incoming to lose — but worth
        # counting, because this row WILL conflict the moment the peer touches it.
        return Decision(SKIP, reason=REASON_LOCAL_AHEAD)

    # Both sides moved. A timestamp may arbitrate, but only a real modification stamp,
    # and only ever visibly.
    if has_timestamp and allow_timestamp_tiebreak and peer_changed_at and local_changed_at:
        if peer_changed_at > local_changed_at:
            return Decision(UPDATE, reason=REASON_TIMESTAMP, auto_resolved=True,
                            conflict_kind=BOTH_CHANGED,
                            detail="Both sides changed; the peer's edit is later.",
                            field_diff=field_diff(local_fields or {}, peer_fields or {}))
        return Decision(SKIP, reason=REASON_TIMESTAMP, auto_resolved=True,
                        conflict_kind=BOTH_CHANGED,
                        detail="Both sides changed; the local edit is later.",
                        field_diff=field_diff(local_fields or {}, peer_fields or {}))

    return _conflict(
        BOTH_CHANGED,
        "Both instances changed this row since they last agreed, and this model has no "
        "modification timestamp to tell which edit came later. Keeping the local row "
        "until you decide.",
        local_fields, peer_fields,
    )


def _conflict(kind: str, detail: str, local_fields, peer_fields) -> Decision:
    return Decision(
        CONFLICT, conflict_kind=kind, detail=detail,
        field_diff=field_diff(local_fields or {}, peer_fields or {}),
    )
