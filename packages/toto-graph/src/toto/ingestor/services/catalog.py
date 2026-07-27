"""Build the detection vocabulary from existing graph entities.

Reads existing nodes through ``bento.graph_service`` (never Neo4j directly) and
turns each node's *searchable* property values + aliases into catalog entries.
These feed both the spaCy EntityRuler (exact existing-entity detection) and the
rapidfuzz duplicate matcher.
"""

import json
from dataclasses import dataclass

from toto.bento import graph_service
from toto.bento.models import BentoCategory

from . import config
from .text import normalize


@dataclass(frozen=True)
class CatalogEntry:
    surface: str          # surface form as stored in the graph
    normalized: str       # normalize(surface) — the match key
    uid: str
    category_slug: str
    display: str
    prop: str             # property that produced this surface, or "alias"


def _alias_values(props, alias_property):
    if not alias_property:
        return []
    raw = (props or {}).get(alias_property)
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw if x]
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed if x]
            except ValueError:
                pass
        return [part.strip() for part in text.split(",") if part.strip()]
    return []


def build_catalog(cap=None):
    """Return ``(entries, meta)``.

    ``entries`` is a list of :class:`CatalogEntry`; ``meta`` reports how many
    nodes were read and whether the global cap truncated the catalog (surfaced to
    the user so a partial catalog never silently reads as complete).
    """
    cap = cap if cap is not None else config.catalog_max_nodes()
    entries = []
    remaining = cap
    node_count = 0
    truncated = False

    for cat in BentoCategory.objects.all():
        if remaining <= 0:
            truncated = True
            break
        search_props = graph_service.searchable_property_names(cat)
        for node in graph_service.iter_nodes(cat_slug=cat.slug, cap=remaining):
            node_count += 1
            remaining -= 1
            props = node.get("properties") or {}
            uid = node.get("uid")
            if not uid:
                continue
            display = node.get("display") or uid
            seen = set()

            def _add(surface, prop):
                key = normalize(surface)
                if not key or key in seen:
                    return
                seen.add(key)
                entries.append(
                    CatalogEntry(surface.strip(), key, uid, cat.slug, display, prop)
                )

            for prop in search_props:
                val = props.get(prop)
                if isinstance(val, str) and val.strip():
                    _add(val, prop)
            for alias in _alias_values(props, cat.alias_property):
                _add(alias, "alias")

            if remaining <= 0:
                truncated = True
                break

    meta = {"node_count": node_count, "entry_count": len(entries), "truncated": truncated}
    return entries, meta
