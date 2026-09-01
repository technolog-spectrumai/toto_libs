"""How far a workspace may turn a dial-backed setting up.

Two of the workspace settings — the kernel's idle lifetime and the LaTeX
compile timeout — were already declared as `toto.quota.times` dials, with a
free default and a ceiling. On a host with a ledger those numbers mean
"what you get, and what you may buy up to": the range between them is gated by
a paid `TimeGrant`, and `times.effective_seconds()` answers with what the user
actually holds.

This host has no ledger. `toto.tax` is not installed, so `effective_seconds()`
can only ever return the free default and the dials are welded shut — which is
why the time card in the room rendered nothing at all.

The rule here follows the platform's own doctrine, "cap always, charge where
there is a ledger" (see zenobia/technology.md): the CEILING is the safety cap and
applies everywhere, while BILLING only gates the range beneath it where a
ledger exists to gate it with. So:

    ledger present  ->  [free, effective_seconds(...)]   the grant binds
    no ledger       ->  [free, ceiling_seconds(...)]     the declaration binds

A workspace setting is then just a choice within that range. It can never
exceed the ceiling on any host, and on a billed host it can never exceed what
was paid for.
"""

from __future__ import annotations

from django.apps import apps

from toto.quota import times


def _has_ledger() -> bool:
    """Whether metered time is something this host can sell."""
    return apps.is_installed("toto.tax")


def range_for(dial_key: str, granted: int) -> tuple[int, int]:
    """The range, given an already-known grant.

    Split out from `allowed_range` so a batch caller — the kernel reaper walks
    every live session — can reuse one `bulk_effective_seconds` lookup instead
    of asking per workspace, without the rule being written twice.
    """
    free = times.free_seconds(dial_key)
    if _has_ledger():
        # effective_seconds already clamps into [free, ceiling]; a grant below
        # the free default cannot narrow the range to nothing.
        return free, max(free, granted)
    return free, max(free, times.ceiling_seconds(dial_key))


def allowed_range(dial_key: str, workspace) -> tuple[int, int]:
    """The (minimum, maximum) seconds a workspace may choose for this dial."""
    granted = times.effective_seconds(dial_key, scope_id=workspace.pk)
    return range_for(dial_key, granted)


def clamp(value, low: int, high: int, *, default: int) -> int:
    """`value` inside [low, high], or `default` when it is unset or nonsense."""
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(number, high))


def dial_default(dial_key: str, workspace) -> int:
    """What applies with nothing stored: the entitlement, unchanged.

    Deliberately NOT the ceiling — an untouched workspace behaves exactly as it
    did before this feature existed.
    """
    if _has_ledger():
        return times.effective_seconds(dial_key, scope_id=workspace.pk)
    return times.free_seconds(dial_key)


def resolve_seconds(dial_key: str, workspace, stored) -> int:
    """The seconds actually in force for this workspace.

    Clamped on the way OUT as well as in, so a ceiling that shrinks (or a grant
    that lapses on a billed host) takes effect at once rather than at the next
    save.
    """
    low, high = allowed_range(dial_key, workspace)
    return clamp(stored, low, high, default=dial_default(dial_key, workspace))
