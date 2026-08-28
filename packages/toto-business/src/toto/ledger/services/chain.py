"""Appending to a chain, and proving one has not been touched.

`append` and `verify` are irena's `append_entry`/`verify_ledger` with their
shapes intact — the `select_for_update` head lock, the idempotent source key,
the contiguity/previous-hash/entry-hash walk, and a verdict that names the
first block that failed rather than just saying "broken". What changed is what
gets hashed: canonical XML text instead of canonical JSON, under an algorithm
the chain itself names.

**Verification never writes.** Not a cached flag, not a `last_verified_at`, not
a repair. A function that could modify the thing it is auditing is not
evidence, and the whole point of this file is that its answer can be trusted
about a database it does not control.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from toto.ledger.canonical import (
    DEFAULT_ALGORITHM,
    FORMAT_VERSION,
    CanonicalError,
    canonical_payload,
    canonicalize,
    digest,
    resolve,
)
from toto.ledger.models import Ledger, LedgerEntry, LedgerKind


@dataclass(frozen=True)
class Verification:
    """What a walk found. `first_bad_sequence` is the whole point of it."""

    ok: bool
    checked: int
    first_bad_sequence: int | None = None
    detail: str = ""

    @property
    def status(self):
        return "healthy" if self.ok else "broken"

    def __bool__(self):
        return self.ok


# ---------------------------------------------------------------------------
# The hashed material
# ---------------------------------------------------------------------------


def block_xml(*, ledger_uid, sequence, previous_hash, payload_xml, uid,
              actor_ref, occurred_at, source_type, source_uid, source_ref,
              algorithm, format_version) -> str:
    """The exact text a block's hash is taken over.

    Derived, never stored: every field it reads is immutable, so recomputing it
    is deterministic, and one representation serves as both the hashed preimage
    and the exported block. Two things that must agree cannot drift when there
    is only one of them.

    The result is passed through `canonicalize()` rather than trusted as
    written. That is what makes an export checkable by somebody who is not
    running this code: an auditor parses the block with whatever XML library
    they have, applies the documented canonical rule, and reaches this same
    text. Hashing the bytes as constructed would instead demand they reproduce
    our exact spelling — and `<previous-hash></previous-hash>` versus
    `<previous-hash/>` is enough to make an honest verifier report a forgery.
    """
    return canonicalize(
        f'<block format="{format_version}" algorithm="{algorithm}">'
        f"<uid>{uid}</uid>"
        f"<ledger>{ledger_uid}</ledger>"
        f"<sequence>{sequence}</sequence>"
        f"<previous-hash>{previous_hash or ''}</previous-hash>"
        f"<occurred-at>{occurred_at.isoformat()}</occurred-at>"
        f"<actor>{_escape(actor_ref or '', quote=False)}</actor>"
        f'<source type="{_escape(source_type or "")}" '
        f'uid="{source_uid or ""}" ref="{_escape(source_ref or "")}"/>'
        f"{payload_xml}"
        f"</block>"
    )


def _escape(value: str, *, quote: bool = True) -> str:
    """`quote=True` for attributes, `quote=False` for text — canonical.py's split."""
    from html import escape

    return escape(str(value), quote=quote)


def entry_block_xml(entry: LedgerEntry) -> str:
    return block_xml(
        ledger_uid=entry.ledger.uid,
        sequence=entry.sequence,
        previous_hash=entry.previous_hash,
        payload_xml=entry.payload_xml,
        uid=entry.uid,
        actor_ref=entry.actor_ref,
        occurred_at=entry.occurred_at,
        source_type=entry.source_type,
        source_uid=entry.source_uid,
        source_ref=entry.source_ref,
        algorithm=entry.algorithm,
        format_version=entry.format_version,
    )


def entry_hash(entry: LedgerEntry) -> str:
    return digest(entry_block_xml(entry), algorithm=entry.algorithm)


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------


GENESIS_REF = "genesis"


def genesis_payload(ledger: Ledger) -> dict:
    """What the first block says. A chain carries the rules for reading itself."""
    return {
        "genesis": True,
        "hash_algorithm": ledger.algorithm,
        "format_version": ledger.format_version,
        "ledger_uid": str(ledger.uid),
        "ledger_key": ledger.key,
        "ledger_name": ledger.name,
        "ledger_kind": ledger.kind,
        "scope_type": ledger.scope_type,
        "scope_uid": str(ledger.scope_uid) if ledger.scope_uid else "",
    }


@transaction.atomic
def open_ledger(*, key: str, name: str, kind=LedgerKind.GENERIC, scope_type="",
                scope_uid=None, description="", algorithm=DEFAULT_ALGORITHM,
                format_version=FORMAT_VERSION, actor=None) -> Ledger:
    """Get-or-create a chain, sealed and with its genesis block written.

    Idempotent: calling it twice returns the same chain with the same genesis,
    which is what makes backfilling existing owners safe to re-run.
    """
    resolve(algorithm)  # refuse an unknown name at creation, not at verify time
    ledger, created = Ledger.objects.get_or_create(
        scope_type=scope_type,
        scope_uid=scope_uid,
        key=key,
        defaults={
            "name": name,
            "kind": kind,
            "description": description,
            "algorithm": algorithm,
            "format_version": format_version,
        },
    )
    if not ledger.entries.exists():
        _append_locked(
            ledger=ledger,
            payload=genesis_payload(ledger),
            actor=actor,
            source_type=LedgerEntry.GENESIS_SOURCE_TYPE,
            source_uid=ledger.uid,
            source_ref=GENESIS_REF,
        )
    return ledger


# ---------------------------------------------------------------------------
# Appending
# ---------------------------------------------------------------------------


@transaction.atomic
def append(*, ledger: Ledger, payload=None, payload_xml: str | None = None,
           actor=None, occurred_at: datetime | None = None, source_type="",
           source_uid=None, source_ref="", signer=None) -> LedgerEntry:
    """Append one block, locking the chain head for the whole transaction.

    Pass `payload` (a plain structure) or `payload_xml` (already-canonical
    text), never both. The lock is what makes the sequence contiguous under
    concurrency; the unique constraints are what makes a fork impossible even
    if the lock were somehow lost.

    `signer`, if given, is called with the block's canonical XML once the hash
    is known and returns `(signature, key_id, algorithm)`. It has to happen
    here: the row refuses every later write, so there is no moment after this
    at which a signature could be attached.
    """
    if ledger.pk is None:
        raise ValidationError("Append to a saved ledger.")
    if not ledger.entries.exists():
        raise ValidationError(
            "This chain has no genesis block. Open it through open_ledger()."
        )
    return _append_locked(
        ledger=ledger, payload=payload, payload_xml=payload_xml, actor=actor,
        occurred_at=occurred_at, source_type=source_type,
        source_uid=source_uid, source_ref=source_ref, signer=signer,
    )


def _append_locked(*, ledger, payload=None, payload_xml=None, actor=None,
                   occurred_at=None, source_type="", source_uid=None,
                   source_ref="", signer=None) -> LedgerEntry:
    if (payload is None) == (payload_xml is None):
        raise ValidationError("Pass exactly one of payload or payload_xml.")

    # Locking the LEDGER row, not the last entry: the head is a moving target
    # and there is nothing to lock before the first append.
    locked = Ledger.objects.select_for_update().get(pk=ledger.pk)

    if source_uid:
        existing = locked.entries.filter(
            source_type=source_type, source_uid=source_uid,
        ).first()
        if existing:
            # Idempotent by source. A retried request must not append twice.
            return existing

    if payload_xml is None:
        payload_xml = canonical_payload(payload)
    else:
        # Trust nothing a caller hands us as "already canonical".
        payload_xml = canonicalize(payload_xml)

    previous = locked.entries.order_by("-sequence").first()
    sequence = previous.sequence + 1 if previous else 1
    previous_hash = previous.entry_hash if previous else ""
    occurred_at = occurred_at or timezone.now()
    actor_ref = ""
    if actor is not None and getattr(actor, "pk", None):
        actor_ref = actor.get_username() or str(actor.pk)

    entry = LedgerEntry(
        ledger=locked,
        sequence=sequence,
        previous_hash=previous_hash,
        payload_xml=payload_xml,
        algorithm=locked.algorithm,
        format_version=locked.format_version,
        actor=actor if getattr(actor, "pk", None) else None,
        actor_ref=actor_ref,
        occurred_at=occurred_at,
        source_type=source_type,
        source_uid=source_uid,
        source_ref=source_ref,
    )
    block = entry_block_xml(entry)
    entry.entry_hash = digest(block, algorithm=entry.algorithm)
    if signer is not None:
        signature, key_id, signature_algorithm = signer(block)
        entry.signature = signature or ""
        entry.signature_key_id = key_id or ""
        entry.signature_algorithm = signature_algorithm or ""
    entry.save()
    return entry


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def chain_algorithm(ledger: Ledger) -> str:
    """The algorithm the GENESIS block names, which is the authoritative copy.

    Reading `Ledger.algorithm` instead would let anyone with UPDATE on one
    unhashed column re-point a chain at a different hash and watch it verify.
    """
    genesis = ledger.entries.order_by("sequence").first()
    if genesis is None:
        return ledger.algorithm
    try:
        recorded = _genesis_field(genesis.payload_xml, "hash_algorithm")
    except CanonicalError:
        return genesis.algorithm
    return recorded or genesis.algorithm


def _genesis_field(payload_xml: str, key: str) -> str:
    from xml.etree import ElementTree

    root = ElementTree.fromstring(payload_xml)
    for entry in root.iter("entry"):
        if entry.get("key") == key:
            value = entry.find("text")
            if value is not None:
                return (value.text or "").strip()
    return ""


def verify(ledger: Ledger, *, source_validator=None) -> Verification:
    """Walk the chain and report the FIRST check that failed.

    Reads only. Returns rather than raises, because "this ledger is broken at
    block 12" is an answer a page has to render, not an exception to swallow.
    """
    algorithm = chain_algorithm(ledger)
    try:
        resolve(algorithm)
    except CanonicalError as exc:
        return Verification(False, 0, None, str(exc))

    previous_hash = ""
    checked = 0
    for entry in ledger.entries.select_related("ledger").order_by("sequence"):
        expected = checked + 1
        if entry.sequence != expected:
            return Verification(False, checked, entry.sequence,
                                "The sequence is not contiguous.")
        if checked == 0 and not entry.is_genesis:
            return Verification(False, checked, entry.sequence,
                                "The chain does not begin with a genesis block.")
        if checked > 0 and entry.is_genesis:
            return Verification(False, checked, entry.sequence,
                                "A second genesis block appears mid-chain.")
        if entry.algorithm != algorithm:
            return Verification(False, checked, entry.sequence,
                                "This block names a different hash algorithm "
                                "than the chain's genesis block.")
        if entry.previous_hash != previous_hash:
            return Verification(False, checked, entry.sequence,
                                "The previous hash does not match the prior block.")
        if entry.entry_hash != digest(entry_block_xml(entry), algorithm=algorithm):
            return Verification(False, checked, entry.sequence,
                                "The block hash does not verify.")
        if source_validator is not None:
            reason = source_validator(entry)
            if reason:
                return Verification(False, checked, entry.sequence, reason)
        previous_hash = entry.entry_hash
        checked += 1

    if checked == 0:
        return Verification(False, 0, None, "The chain has no genesis block.")
    return Verification(True, checked)
