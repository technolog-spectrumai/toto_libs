"""What of a node's properties a person may see: never its embedding (2026-10-12).

Vector search stores a node's embedding as an ordinary Neo4j property, next to its
text: ``ravioli_reindex_embeddings`` writes ``TOTO_VECTOR_EMBEDDING_PROPERTY``
(``embedding`` by default, or whatever ``--embedding-property`` names), neo4j-graphrag's
lexical graph writes ``Chunk.embedding``, and a host may keep a signature of the model
beside it (wotan's ``embedding_signature``). Every reader that serialized raw
properties — the Knowledge Graph viewer, the stored query results, search, GraphRAG,
history, the NeoJSON exports and editor — put hundreds of floats in front of the person,
into the vault and into the model's prompt. A vector is derived data: nobody reads it,
and the index that needs it reads Neo4j, not these dicts.

So one rule, applied where properties leave Neo4j for a page, an API, a stored row, a
file or a prompt. A property is hidden when

- its **name** says embedding: it is ``TOTO_VECTOR_EMBEDDING_PROPERTY``, or one of
  ``RAVIOLI_HIDDEN_PROPERTIES`` (a host's own names), or it contains ``embedding`` in
  any case (``embedding``, ``chunk_embedding``, ``embedding_vector``,
  ``embedding_signature``, ``embeddingModel``). The signature beside a vector is a short
  string, but it is embedding bookkeeping, not content: the owner wants no embedding
  noise, so it goes too.
- or its **value** is a vector under a name nobody told us: a list or tuple of at least
  ``VECTOR_MIN_LENGTH`` real numbers, at least one of them a float, or a driver
  ``Vector`` value. Coordinates (``[lon, lat]``), short arrays and lists of integer ids
  stay.

Only on the way out. Nothing here is used where properties are written back to Neo4j or
fed to the index (``update_node`` sets ``n = $props`` and would erase what it was not
given); the writers that load exports use ``SET n += $props``, so a file without the
vector never removes the stored one.
"""
from __future__ import annotations

import re
from numbers import Real

from django.conf import settings

# wotan's smallest embedding dimension is 32; real models are 384 to 3072.
VECTOR_MIN_LENGTH = 32

_KNOWN = ("embedding", "embedding_signature")


def hidden_property_names() -> set[str]:
    """The property names hidden by name: the known ones, the configured vector
    property, and a host's ``RAVIOLI_HIDDEN_PROPERTIES``."""
    names = set(_KNOWN)
    try:
        configured = getattr(settings, "TOTO_VECTOR_EMBEDDING_PROPERTY", None)
        extra = getattr(settings, "RAVIOLI_HIDDEN_PROPERTIES", None) or ()
    except Exception:  # noqa: BLE001 — no Django settings (a script using neojson): the defaults
        configured, extra = None, ()
    if configured:
        names.add(str(configured))
    names.update(str(n) for n in extra)
    return names


def is_vector(value) -> bool:
    """True for a value that is an embedding whatever its name: a driver vector type,
    or a long list of real numbers with a float among them (bools are not numbers
    here, and an all-integer list is ids, not a vector)."""
    if type(value).__name__ == "Vector":  # neo4j driver's native vector type
        return True
    if not isinstance(value, (list, tuple)) or len(value) < VECTOR_MIN_LENGTH:
        return False
    if not all(isinstance(x, Real) and not isinstance(x, bool) for x in value):
        return False
    return any(isinstance(x, float) for x in value)


def is_hidden(name, value=None, *, names=None) -> bool:
    """True when the property ``name`` holding ``value`` must not be shown."""
    key = str(name)
    if key in (names if names is not None else hidden_property_names()):
        return True
    if "embedding" in key.lower():
        return True
    return is_vector(value)


def visible_props(props) -> dict:
    """A copy of ``props`` without its hidden properties. Takes anything ``dict()``
    takes (a dict, a neo4j Node or Relationship) or None, which gives ``{}``.

    Call it before any stringifying (``_to_json_safe``, ``str``): the value test needs
    the list, not its text."""
    if not props:
        return {}
    names = hidden_property_names()
    return {k: v for k, v in dict(props).items() if not is_hidden(k, v, names=names)}


def visible_items(items, key: str = "props") -> list:
    """``items`` (dicts such as stored nodes or edges) with ``visible_props`` applied to
    each one's ``key``. For rows stored before the rule existed: a cached query result
    or a workflow's output still holds the vectors it was saved with."""
    out = []
    for item in items or []:
        if isinstance(item, dict) and isinstance(item.get(key), dict):
            item = {**item, key: visible_props(item[key])}
        out.append(item)
    return out


_NUMBER = r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
_VECTOR_TEXT = re.compile(
    r"\[\s*(?:" + _NUMBER + r"\s*,\s*){%d,}" % (VECTOR_MIN_LENGTH - 1) + _NUMBER + r"\s*\]"
)


def hide_vectors_in_text(text):
    """``text`` with every printed vector (a bracketed run of ``VECTOR_MIN_LENGTH`` or
    more numbers) replaced by ``[vector hidden]``. Only for text already stored — a
    GraphRAG context saved before the formatter learnt the rule — where the structure
    is gone and only its print remains."""
    if not isinstance(text, str) or "[" not in text:
        return text
    return _VECTOR_TEXT.sub("[vector hidden]", text)
