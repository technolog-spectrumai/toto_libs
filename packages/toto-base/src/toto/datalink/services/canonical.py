"""Canonical forms: the one way a row is turned into bytes, for hashing.

Three things are hashed, and all three go through ``canonical_json`` so that two
instances running the same code agree byte for byte:

* an **identity** -> ``identity_hash``, the key the merge base is stored under;
* a **row payload** -> ``row_checksum``, the "has this changed?" primitive;
* the **registry** -> ``registry_digest`` (in ``registry.py``), the compatibility check.

The row checksum is what makes "newest change wins" possible on a scope where most
models have no modification timestamp. It answers a question a timestamp cannot:
*did this side edit the row since the two instances last agreed?* See
``services/decide.py`` for the three-way table it feeds.

Adapted from ``ravioli.graph_export.content_checksum``, which does the same job for
Neo4j nodes. Two deliberate differences: that function filters out Neo4j's reserved
bookkeeping properties, which have no analogue here; and it hashes properties only,
whereas a row's m2m membership is part of what "changed" means, so both are hashed
together — otherwise adding someone to a community would be invisible to the engine.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from django.core.serializers.json import DjangoJSONEncoder


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no incidental whitespace.

    ``DjangoJSONEncoder`` so datetimes, dates, Decimals and UUIDs encode the same way
    they do on the wire — a checksum computed over a different encoding of the same
    value would report a phantom change on every run.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), cls=DjangoJSONEncoder)


def identity_hash(model_label: str, identity: Mapping[str, Any]) -> str:
    """A stable key for "this row, on either instance".

    The model label is part of the hash so two models cannot collide on the same
    natural-key value — ``socialhub.Constitution`` and ``locations.MapLayer`` both key
    on a field called ``slug``.
    """
    return hashlib.sha256(
        canonical_json({"model": model_label, "identity": dict(identity)}).encode("utf-8")
    ).hexdigest()


def row_checksum(fields: Mapping[str, Any], m2m: Mapping[str, Any] | None = None) -> str:
    """The content fingerprint of one row, as the policy sees it.

    Only what the policy declares is hashed, which is what makes the checksum
    meaningful: a column datalink does not replicate must not make a row look changed.

    M2M values must already be sorted by their canonical identity — see
    ``sorted_refs``. An unordered list would make the checksum flap between runs and
    manufacture conflicts out of nothing.
    """
    return hashlib.sha256(
        canonical_json({"fields": dict(fields), "m2m": dict(m2m or {})}).encode("utf-8")
    ).hexdigest()


def ref_sort_key(ref: Mapping[str, Any]) -> str:
    """Order references deterministically, whatever kind of identity they carry."""
    if "uid" in ref:
        return f"uid:{ref['uid']}"
    return "natural:" + canonical_json(ref.get("natural", {}))


def sorted_refs(refs: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Sort an m2m reference list into its canonical order.

    Called on both sides — the emitter before hashing and sending, the receiver before
    hashing what it just wrote — so the two agree. Sorting here rather than relying on
    a queryset's ordering also means a model whose Meta.ordering changes does not
    silently invalidate every stored merge base.
    """
    return sorted(refs, key=ref_sort_key)
