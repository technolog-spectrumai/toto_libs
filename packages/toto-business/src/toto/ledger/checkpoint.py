"""Ledger checkpoints: the chain's head, foldable onto paper.

Unparked from `zenobia/limbo/polls_governance/checkpoint.py`, where it guarded
polls Decisions. It was parked when governance left Zenobia, not because it was
wrong. The fold, the netstring framing, the payload format and the verdicts are
byte-for-byte the original; what changed is what a row IS — a `LedgerEntry`
instead of a `Decision` — and one layer of `_localize` that this ledger makes
unnecessary. See :func:`entry_digest` and :func:`_localize` for both.

``chain.verify`` detects a careless edit — one row changed, every
later hash stops verifying. What it CANNOT detect is a careful one: an
attacker with database write access who edits ``content`` and rewrites
``content_hash``/``prev_hash`` for every later row produces a chain that is
internally consistent and verifies clean. The chain proves the rows agree
with each other; it cannot prove they are the rows that were there yesterday.

A checkpoint closes that hole by putting the head OUTSIDE the database: a QR
code on paper, in a safe, on another machine. The attacker can rewrite every
row and every stored checkpoint; the printout does not change. Verifying is
one walk that recomputes the fold from the stored blocks and compares —
byzantine fault tolerance in its offline form: the evidence does not have to
trust the database it convicts.

**What the fold covers here.** Each block's digest takes its stored
``entry_hash`` AND the content that hash is supposed to describe —
``payload_xml``, the timestamps, the source and actor references. Not the hash
alone: a fold over the stored hash would be asking the chain whether the chain
is intact, and a stored hash is precisely what an attacker rewrites. Folding
the content makes the checkpoint an independent witness rather than a second
opinion from the same source.

**Netstrings, not concatenation.** ``scope_id`` is a free-form CharField and
several fields are user text; length-prefixing every piece makes the preimage
injective, so no combination of values can collide with another by sliding a
boundary. (The chain's own block XML delimits every field with tags, which buys the same
property a different way; the fold keeps netstrings because it concatenates
opaque digests rather than markup.)

**A fold, not a Merkle root, on purpose.** ``head(n)`` is a running hash over
entries 1..n, so ONE walk yields the head at every count along the way. An
old checkpoint of a since-grown ledger verifies as a prefix (MATCH at its
count, reported as such); growth is never mistaken for tampering, and every
stored checkpoint of a scope is verifiable in a single pass. What a fold
gives up (log-size membership proofs) nothing here needs.

Multiple independent checkpoints are the intended shape: each signer keeps
their own printout, and when a self-consistent rewrite is detected, the set
of stored checkpoints brackets WHEN — the newest one that still verifies
bounds the last honest state.
"""
from __future__ import annotations

import dataclasses
import hashlib
from urllib.parse import parse_qsl, quote, unquote, urlencode

from django.utils import timezone

#: The algorithm tag carried in every payload. Bump only with a new verifier
#: arm — an unknown tag must refuse, never guess.
ALGORITHM = "sha256-fold-ns1"

#: The payload scheme prefix. A scanned QR either starts with this or it is
#: not a ledger checkpoint.
SCHEME = "totoledger:"


def _ns(raw: bytes) -> bytes:
    """Netstring: ``len:bytes`` — the length prefix is what makes the
    preimage injective."""
    return str(len(raw)).encode("ascii") + b":" + raw


def _s(text) -> bytes:
    return _ns(str(text if text is not None else "").encode("utf-8"))


def entry_digest(entry) -> bytes:
    """One block's contribution to the fold.

    Folds the block's CONTENT, not merely its claimed hash. That distinction
    is the whole point and it was nearly lost: an earlier draft folded
    `entry_hash` alone, on the reasoning that the hash already commits to
    everything. It does — but only if the hash is honest, and a stored hash is
    exactly the thing an attacker with database access rewrites. A fold over
    `entry_hash` asks the chain whether the chain is intact, which is not an
    independent question. `tests/test_checkpoint.py` caught this: a payload
    edited without recomputing its hash left the chain visibly broken and the
    fold still reporting MATCH.

    So `payload_xml` goes in as itself. This is the parked version's reasoning
    exactly — it folded every denormalized column the ledger page rendered,
    for the same reason — reached through different columns.
    """
    h = hashlib.sha256()
    for piece in (
        entry.entry_hash,
        entry.previous_hash,
        entry.payload_xml,
        entry.sequence,
        entry.uid,
        entry.algorithm,
        entry.occurred_at.isoformat(),
        entry.source_type,
        entry.source_ref,
        entry.actor_ref,
    ):
        h.update(_s(piece))
    return h.digest()


def fold_heads(entries):
    """Yield ``(count, head_hex, pk)`` for every prefix of the walk.

    One pass, heads at every count — verifying an old checkpoint is reading
    the tuple at its count, not a second walk.
    """
    head = b""
    count = 0
    for entry in entries:
        head = hashlib.sha256(_ns(head) + _ns(entry_digest(entry))).digest()
        count += 1
        yield count, head.hex(), entry.pk


#: What a checkpoint's `s` field says. A checkpoint is taken of ONE chain, and
#: a chain is named by its uid — so the payload's scope pair is ("ledger", uid)
#: and this module never needs to know what owns that chain.
SCOPE_TYPE = "ledger"


def _entries(scope_id: str):
    from .models import LedgerEntry

    return (LedgerEntry.objects
            .filter(ledger__uid=scope_id)
            .order_by("sequence")
            .iterator())


def head_at(scope_type: str, scope_id: str, upto: int | None = None):
    """``(count, head_hex)`` for a chain — the whole thing, or a prefix."""
    count, head_hex = 0, ""
    if scope_type != SCOPE_TYPE or not scope_id:
        return count, head_hex
    for count_i, head_i, _pk in fold_heads(_entries(scope_id)):
        count, head_hex = count_i, head_i
        if upto is not None and count >= upto:
            break
    return count, head_hex


# ---------------------------------------------------------------------------
# The payload — what lives inside the QR
# ---------------------------------------------------------------------------

def build_payload(*, scope_type: str, scope_id: str, entry_count: int,
                  head_hex: str, taken_at=None, site: str = "",
                  signature: str = "", key_id: str = "") -> str:
    """The exact text the QR carries. Everything the verifier needs, nothing
    it must look up: scope, count, head, algorithm, timestamp — and, when the
    checkpoint is signed, the signature and the key fingerprint."""
    taken_at = taken_at or timezone.now()
    fields = [
        ("v", "1"),
        ("a", ALGORITHM),
        ("s", scope_type or ""),
        ("i", scope_id or ""),
        ("n", str(entry_count)),
        ("h", head_hex),
        ("t", taken_at.isoformat(timespec="seconds")),
    ]
    if site:
        fields.append(("p", site))
    if signature:
        fields.append(("sig", signature))
        fields.append(("k", key_id))
    return SCHEME + urlencode(fields, quote_via=quote)


class PayloadError(ValueError):
    """The scanned text is not a ledger checkpoint, and the message says why."""


def parse_payload(text: str) -> dict:
    text = (text or "").strip()
    if not text.startswith(SCHEME):
        raise PayloadError(
            "This is not a ledger checkpoint QR — the payload does not start "
            f"with '{SCHEME}'.")
    fields = dict(parse_qsl(text[len(SCHEME):], keep_blank_values=True))
    if fields.get("v") != "1":
        raise PayloadError(
            f"Unknown checkpoint version {fields.get('v')!r} — this verifier "
            "reads version 1.")
    if fields.get("a") != ALGORITHM:
        raise PayloadError(
            f"Unknown algorithm {fields.get('a')!r} — this verifier "
            f"recomputes {ALGORITHM} and refuses to guess.")
    missing = [key for key in ("n", "h", "t") if not fields.get(key)]
    if missing:
        raise PayloadError(
            f"The checkpoint is missing {', '.join(missing)} — scan the whole "
            "code, or paste the full text.")
    try:
        count = int(fields["n"])
    except ValueError:
        raise PayloadError("The entry count is not a number.")
    return {
        "scope_type": unquote(fields.get("s", "")),
        "scope_id": unquote(fields.get("i", "")),
        "entry_count": count,
        "head_hex": fields["h"],
        "taken_at": fields["t"],
        "site": fields.get("p", ""),
        "signature": fields.get("sig", ""),
        "key_id": fields.get("k", ""),
    }


# ---------------------------------------------------------------------------
# Verification — MATCH / MISMATCH, and WHERE
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Verification:
    """What comparing one checkpoint against the stored ledger concluded.

    ``verdict`` is one of:

    MATCH             — the ledger at the checkpoint's count folds to its head,
                        and the ledger has not grown since.
    MATCH_GROWN       — the prefix matches; the ledger has grown past the
                        checkpoint. Growth is the ledger working, not tampering.
    MISMATCH          — the recomputed fold differs. ``detail`` says where the
                        damage is localized (a broken chain row, a display
                        column contradicting its own content) or states that
                        the rewrite is self-consistent and can only be
                        bracketed by an earlier checkpoint.
    TRUNCATED         — the scope holds FEWER entries than the checkpoint
                        counted: rows were deleted outright.
    EMPTY_SCOPE       — the checkpoint names a scope with no decisions at all.
    """

    verdict: str
    checked: int
    expected_count: int
    recomputed_head: str
    expected_head: str
    detail: str = ""
    first_bad_pk: int | None = None
    signature: str = ""       # carried through for the signature layer
    key_id: str = ""
    signature_state: str = ""  # "", "valid", "invalid", "unknown-key"


def _localize(scope_type: str, scope_id: str, upto: int) -> tuple[str, int | None]:
    """When the fold mismatches, say where — as precisely as the data allows.

    The parked version had three layers. The middle one is gone here, and its
    absence is a property of this ledger rather than a lost feature: it looked
    for a display column contradicting its row's own hashed content, and every
    column of a LedgerEntry is inside `entry_hash`. There is no such gap left
    to find.

    So: either the chain itself is broken and `chain.verify` names the first
    bad block, or the chain verifies and the fold still differs — which means
    the history was rewritten self-consistently, and only an earlier
    checkpoint can bracket when.
    """
    from .models import Ledger
    from .services import chain as chain_service

    ledger = Ledger.objects.filter(uid=scope_id).first()
    if ledger is None:
        return ("The chain this checkpoint names no longer exists on this "
                "host.", None)

    result = chain_service.verify(ledger)
    if not result.ok:
        return (f"The hash chain itself is broken at block "
                f"{result.first_bad_sequence}: {result.detail} Every block "
                "from there on is unverifiable, and the fold cannot match.",
                result.first_bad_sequence)

    return ("The chain verifies block by block and the fold still differs: "
            "the history was rewritten self-consistently — content edited and "
            "every later hash recomputed. The database cannot say when; an "
            "earlier stored or offline checkpoint brackets it, and the newest "
            "one that still matches bounds the last honest state.", None)


def verify(parsed: dict) -> Verification:
    """Recompute the chain's fold from its stored blocks and compare."""
    from .models import LedgerEntry

    scope_type = parsed["scope_type"]
    scope_id = parsed["scope_id"]
    expected_count = parsed["entry_count"]
    expected_head = parsed["head_hex"]

    total = LedgerEntry.objects.filter(ledger__uid=scope_id).count() \
        if scope_type == SCOPE_TYPE and scope_id else 0
    common = dict(expected_count=expected_count, expected_head=expected_head,
                  signature=parsed.get("signature", ""),
                  key_id=parsed.get("key_id", ""))

    if total == 0:
        return Verification(
            verdict="EMPTY_SCOPE", checked=0, recomputed_head="",
            detail="The chain named by this checkpoint holds no blocks "
                   "at all on this host.", **common)
    if total < expected_count:
        return Verification(
            verdict="TRUNCATED", checked=total, recomputed_head="",
            detail=f"The checkpoint counted {expected_count} entries; only "
                   f"{total} exist now — {expected_count - total} were "
                   "deleted outright.", **common)

    count, head_hex = head_at(scope_type, scope_id, upto=expected_count)
    if head_hex == expected_head:
        if total == expected_count:
            return Verification(verdict="MATCH", checked=count,
                                recomputed_head=head_hex, **common)
        return Verification(
            verdict="MATCH_GROWN", checked=count, recomputed_head=head_hex,
            detail=f"The ledger has grown to {total} entries since this "
                   "checkpoint; its prefix still matches exactly.", **common)

    detail, first_bad_pk = _localize(scope_type, scope_id, expected_count)
    return Verification(verdict="MISMATCH", checked=count,
                        recomputed_head=head_hex, detail=detail,
                        first_bad_pk=first_bad_pk, **common)


# ---------------------------------------------------------------------------
# Taking and bracketing
# ---------------------------------------------------------------------------

def take(*, ledger=None, scope_type: str = SCOPE_TYPE, scope_id: str = "",
         by=None, note: str = ""):
    """Fold the scope now, store the row, and return it. The caller renders
    the payload as a QR and tells the operator to keep a copy OUTSIDE the
    database — the stored row is the convenience, the printout is the
    evidence."""
    import os

    from .models import LedgerCheckpoint

    if ledger is not None:
        scope_type, scope_id = SCOPE_TYPE, str(ledger.uid)
    count, head_hex = head_at(scope_type, scope_id)
    if not head_hex:
        # An empty ledger folds to "", and build_payload would then write a
        # payload whose `h` is blank — which parse_payload refuses by design
        # ("scan the whole code"). The row is append-only and delete() raises,
        # so one click on "Take a checkpoint" before the first decision used to
        # leave an unparseable, undeletable witness that broke the checkpoints
        # page for good. There is also nothing to witness: a checkpoint of no
        # decisions attests to nothing.
        raise ValueError(
            "There is nothing to check point yet — this chain has no blocks. "
            "Take one after the first block is recorded.")
    taken_at = timezone.now()
    payload = build_payload(
        scope_type=scope_type, scope_id=scope_id, entry_count=count,
        head_hex=head_hex, taken_at=taken_at,
        site=os.environ.get("PLATFORM_NAME", ""))
    return LedgerCheckpoint.objects.create(
        scope_type=scope_type, scope_id=scope_id,
        entry_count=count, head_hash=head_hex, algorithm=ALGORITHM,
        payload=payload, taken_at=taken_at, taken_by=by, note=note)


def verify_stored(checkpoint) -> Verification:
    return verify(parse_payload(checkpoint.payload))


def verify_text(text: str) -> Verification:
    """Verify a pasted or scanned payload against the live chain.

    The other public entry point, beside :func:`take`. Needs no stored row: a
    QR printed a year ago verifies against a database whose checkpoint table
    was emptied — which is exactly what makes the paper copy evidence against
    the database it convicts.
    """
    return verify(parse_payload(text))


def bracket(scope_type: str = "", scope_id: str = ""):
    """When the current fold no longer matches, date the damage.

    Walks the scope's STORED checkpoints newest-first and returns
    ``(newest_matching, oldest_failing)`` — the rewrite happened after the
    first and no later than the second. Either side may be None. Only as
    trustworthy as the stored rows; an offline printout that fails while the
    stored rows all pass means the stored checkpoints were rewritten too,
    which is itself a finding.
    """
    from .models import LedgerCheckpoint

    newest_matching = None
    oldest_failing = None
    for checkpoint in (LedgerCheckpoint.objects
                       .filter(scope_type=scope_type,
                               scope_id=str(scope_id or ""))
                       .order_by("-entry_count", "-taken_at")):
        if verify_stored(checkpoint).verdict in ("MATCH", "MATCH_GROWN"):
            newest_matching = checkpoint
            break
        oldest_failing = checkpoint
    return newest_matching, oldest_failing
