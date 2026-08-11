"""Reading the monetary history: the head, and whether it holds together.

``chain.py`` is the arithmetic and touches no database. This is the part that
does. Keeping them apart means the hashing can be tested without a migration,
and the walk can be tested without a keypair.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .chain import GENESIS_PREV, compute_event_hash


def chain_head():
    """The last monetary event written anywhere, or None.

    "Anywhere" is literal: one chain covers every currency, so the head is a
    property of the platform rather than of a currency.
    """
    from .models import CurrencyMintEvent

    return CurrencyMintEvent.objects.order_by("-sequence").first()


def head_hash() -> str:
    """What the next event must name as its ``prev_hash``."""
    head = chain_head()
    return head.event_hash if head is not None else GENESIS_PREV


@dataclass
class ChainVerdict:
    """Whether the whole history holds together, and where it does not."""

    ok: bool
    checked: int = 0
    findings: list = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


def verify_chain() -> ChainVerdict:
    """Walk every monetary event and check all three things that can rot.

    Linkage, recomputation, signature — separately, because they fail for
    different reasons and an operator needs to know which. A rewritten amount
    breaks the hash; a hand-inserted row breaks the linkage; a row signed by
    something that is not the issuer breaks the signature. Reporting "the chain
    is bad" without saying which would leave the diagnosis to guesswork.

    The walk is deliberately exhaustive rather than stopping at the first
    problem: an operator fixing a compromised platform wants the whole list.
    """
    from toto.assets.models import CurrencyIssuer

    from .chain import verify_event
    from .models import CurrencyMintEvent

    findings = []
    expected_prev = GENESIS_PREV
    expected_sequence = 0
    keys = {i.fingerprint: i.public_key_pem
            for i in CurrencyIssuer.objects.all()}

    events = list(CurrencyMintEvent.objects.order_by("sequence"))
    for event in events:
        where = f"event #{event.sequence}"

        if event.prev_hash != expected_prev:
            findings.append(
                f"{where} names “{event.prev_hash[:20] or '(root)'}” as its "
                f"predecessor, but the event before it hashes to "
                f"“{expected_prev[:20] or '(root)'}”.")
        if event.sequence != expected_sequence:
            findings.append(
                f"{where} sits where #{expected_sequence} should be — the "
                "sequence has a gap or a repeat.")

        try:
            recomputed = compute_event_hash(event.payload)
        except Exception as exc:  # noqa: BLE001 - a malformed row is a finding
            findings.append(f"{where} carries a payload that is not an "
                            f"event at all: {exc}")
            recomputed = None

        if recomputed is not None and recomputed != event.event_hash:
            findings.append(
                f"{where} does not hash to the name it was stored under; its "
                "payload has been altered since it was written.")

        pem = keys.get(event.issuer_fingerprint)
        if pem is None:
            findings.append(
                f"{where} was signed by issuer "
                f"{event.issuer_fingerprint[:12]}…, which this platform does "
                "not know. Its authority cannot be checked.")
        elif not verify_event(pem, event.payload, event.signature):
            findings.append(
                f"{where} does not verify against the issuer it names.")

        expected_prev = event.event_hash
        expected_sequence = event.sequence + 1

    return ChainVerdict(ok=not findings, checked=len(events),
                        findings=findings)
