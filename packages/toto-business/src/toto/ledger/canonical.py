"""Deterministic XML, and the hash the chain is built on.

Two rules govern everything here.

**The bytes are the truth.** A block's payload is frozen as canonical XML text
and that text is what gets hashed — not a dict that happens to serialize the
same way today. Re-serializing a structure a year from now must produce the
identical string or the chain stops verifying, so serialization is defined
here, versioned by :data:`FORMAT_VERSION`, and never left to a library's
defaults.

**The preimage is injective.** Every value carries its type as its tag and
every container delimits its members, so no two different structures can
produce the same text. That is the same property netstring length-prefixing
buys in the checkpoint fold, reached a different way: a boundary cannot slide
when a tag closes it.

**The algorithm is a name, not an import.** The chain records which hash it was
built with, in its genesis block and on every entry, and :func:`resolve` turns
that recorded name back into a function. Nothing here calls ``hashlib.sha256``
directly. SHA-256 is the default because it is the interoperable choice — a
third party with no access to this code can verify an export with any standard
tool — but a chain sealed with something else stays verifiable forever, and an
algorithm this build does not know refuses rather than guessing.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal
from html import escape
from xml.etree import ElementTree

#: The serialization contract. Bump ONLY for a change that alters the bytes a
#: given structure produces — every existing chain records the version it was
#: sealed under, and a verifier must read the old rules for an old block.
FORMAT_VERSION = "bc-ledger-xml-1"

#: What a new chain is sealed with unless the caller names something else.
DEFAULT_ALGORITHM = "sha256"

#: Every hash this build can verify with. Adding a name here is additive and
#: safe; removing one makes every chain sealed with it unverifiable, so treat
#: this dict as append-only in practice.
ALGORITHMS = {
    "sha256": hashlib.sha256,
    "sha384": hashlib.sha384,
    "sha512": hashlib.sha512,
    "sha3_256": hashlib.sha3_256,
    "blake2b": hashlib.blake2b,
}

#: Structural budgets. A payload is a record of a fact, not a data structure —
#: anything past these is a caller mistake, and refusing early beats a verifier
#: that recurses until the stack gives out.
MAX_DEPTH = 32
MAX_NODES = 5000


class CanonicalError(ValueError):
    """A payload could not be serialized, parsed or canonicalized."""


class UnknownAlgorithm(CanonicalError):
    """A recorded hash name this build cannot resolve.

    Raised rather than defaulted, deliberately: silently verifying an unknown
    chain with SHA-256 would report a healthy ledger it never actually checked.
    """


def resolve(algorithm: str):
    """The hash function a recorded name stands for."""
    try:
        return ALGORITHMS[algorithm]
    except KeyError:
        known = ", ".join(sorted(ALGORITHMS))
        raise UnknownAlgorithm(
            f"This build cannot verify {algorithm!r} chains. Known: {known}."
        ) from None


def digest(text: str, *, algorithm: str = DEFAULT_ALGORITHM) -> str:
    """Hex digest of ``text`` under the named algorithm."""
    return resolve(algorithm)(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Python structure -> canonical XML
# ---------------------------------------------------------------------------


def _text(value: str) -> str:
    return escape(value, quote=False)


def _render(value, *, depth: int, budget: list) -> str:
    budget[0] += 1
    if budget[0] > MAX_NODES:
        raise CanonicalError("Payload exceeded the node budget.")
    if depth > MAX_DEPTH:
        raise CanonicalError("Payload exceeded the maximum depth.")

    # bool BEFORE int: bool is a subclass of int, and <int>1</int> for True
    # would make True and 1 the same preimage.
    if value is None:
        return "<null/>"
    if isinstance(value, bool):
        return f"<bool>{'true' if value else 'false'}</bool>"
    if isinstance(value, int):
        return f"<int>{value}</int>"
    if isinstance(value, Decimal):
        # str(), never float(): the exactness of the register is the whole
        # point, and repr through float would round 0.000001 away.
        return f"<decimal>{_text(str(value))}</decimal>"
    if isinstance(value, float):
        raise CanonicalError(
            "Floats are not canonical — pass a Decimal or a string instead."
        )
    if isinstance(value, dt.datetime):
        return f"<datetime>{_text(value.isoformat())}</datetime>"
    if isinstance(value, dt.date):
        return f"<date>{_text(value.isoformat())}</date>"
    if isinstance(value, str):
        return f"<text>{_text(value)}</text>"
    if isinstance(value, (list, tuple)):
        items = "".join(
            f"<item>{_render(item, depth=depth + 1, budget=budget)}</item>"
            for item in value
        )
        return f"<list>{items}</list>"
    if isinstance(value, dict):
        entries = []
        for key in sorted(value, key=str):
            if not isinstance(key, str):
                raise CanonicalError("Payload map keys must be strings.")
            rendered = _render(value[key], depth=depth + 1, budget=budget)
            entries.append(
                f'<entry key="{escape(key, quote=True)}">{rendered}</entry>'
            )
        return f"<map>{''.join(entries)}</map>"
    raise CanonicalError(f"Cannot canonicalize {type(value).__name__}.")


def canonical_payload(value, *, root: str = "payload") -> str:
    """One frozen payload, as canonical XML text."""
    body = _render(value, depth=0, budget=[0])
    return f"<{root}>{body}</{root}>"


# ---------------------------------------------------------------------------
# XML text -> canonical XML text
# ---------------------------------------------------------------------------


def _reject_unsafe(source: str):
    upper = (source or "").upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise CanonicalError("DOCTYPE and ENTITY declarations are not allowed.")


def _attrs(node) -> str:
    pairs = [
        f'{key}="{escape(value, quote=True)}"'
        for key, value in sorted(node.attrib.items())
    ]
    return (" " + " ".join(pairs)) if pairs else ""


def _canonical_node(node, depth: int = 0) -> str:
    if depth > MAX_DEPTH:
        raise CanonicalError("Document exceeded the maximum depth.")
    text = (node.text or "").strip()
    children = "".join(_canonical_node(child, depth + 1) for child in node)
    # `quote=False` for TEXT, `quote=True` for ATTRIBUTES (in `_attrs`). That
    # split is not cosmetic: `_render` writes text the same way, and if the two
    # disagreed about apostrophes then the same content would hash differently
    # depending on whether it arrived as a structure or as XML text — a chain
    # that verifies from one door and not the other.
    body = escape(text, quote=False) + children if children else escape(text, quote=False)
    return f"<{node.tag}{_attrs(node)}>{body}</{node.tag}>"


def canonicalize(source: str) -> str:
    """Re-serialize XML text into its canonical form.

    Attribute order, insignificant whitespace and entity spelling all stop
    mattering; anything else is a difference the hash is meant to catch. This
    is what lets a verifier accept a pretty-printed export and still reach the
    same digest.
    """
    _reject_unsafe(source)
    try:
        root = ElementTree.fromstring(source)
    except ElementTree.ParseError as exc:
        raise CanonicalError(f"Malformed XML: {exc}") from exc
    return _canonical_node(root)
