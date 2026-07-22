"""LLM (OpenAI) extraction strategy → the same reviewable proposal contract.

Asks an OpenAI chat model to extract entities + relationships constrained to the
Bento templates, maps the JSON to the ingestor's proposal shape, then runs the
existing validation/summarize so the review + apply flow is unchanged.
"""
from __future__ import annotations

import json
import logging

from django.conf import settings

from .. import validation
from .base import MODE_REVIEW, IngestStrategy, existing_nodes_in_text, persist_review

logger = logging.getLogger(__name__)


def _bento_schema():
    """Return (categories, edge_types, searchable) for the prompt + slug lookups.

    Only NON-internal categories are offered to the LLM. ``searchable`` maps each
    category slug to the property the ingestor should populate as its identifier
    (so e.g. a 'kanban-doc-section' node gets its required ``title``, not ``name``).
    """
    from toto.bento.graph_service import searchable_property_names
    from toto.bento.models import BentoCategory, BentoEdgeType

    cats = list(BentoCategory.objects.filter(internal=False))
    categories = {c.slug: c.name for c in cats}
    searchable = {c.slug: searchable_property_names(c)[0] for c in cats}
    edge_types = {}
    for et in BentoEdgeType.objects.prefetch_related("allowed_sources", "allowed_targets"):
        edge_types[et.slug] = {
            "name": et.name,
            "rel_type": et.rel_type,
            "sources": list(et.allowed_sources.values_list("slug", flat=True)),
            "targets": list(et.allowed_targets.values_list("slug", flat=True)),
        }
    return categories, edge_types, searchable


def _prompt(text, categories, edge_types, existing_nodes=None):
    cats = "\n".join(f"  - {slug}: {name}" for slug, name in categories.items())
    edges = "\n".join(
        f"  - {slug}: {e['name']} (from {e['sources'] or 'any'} to {e['targets'] or 'any'})"
        for slug, e in edge_types.items()
    )
    existing_block = ""
    if existing_nodes:
        listing = "\n".join(
            f"  - existing_uid={n['uid']} ({n['category_slug']}): {n['display']}"
            for n in existing_nodes
        )
        existing_block = (
            "\n\nThese EXISTING graph nodes are named in the text. To link to one, emit a "
            "node object with its \"existing_uid\" (omit category_slug/properties) and "
            "reference its temp_id in relationships — do NOT create a duplicate new node:\n"
            f"{listing}"
        )
    return (
        "Extract a knowledge graph from the text below. Use ONLY these node "
        f"categories (by slug):\n{cats}\n\nand ONLY these relationship types (by slug):\n{edges}"
        f"{existing_block}\n\n"
        "Return STRICT JSON: {\"nodes\": [{\"temp_id\": \"n1\", \"category_slug\": \"...\", "
        "\"display\": \"...\", \"properties\": {\"name\": \"...\"}}], \"relationships\": "
        "[{\"temp_id\": \"r1\", \"edge_type_slug\": \"...\", \"from\": \"n1\", \"to\": \"n2\", "
        "\"properties\": {}}]}. Each node is EITHER new (category_slug + properties) OR an "
        "existing one (\"existing_uid\" only). temp_ids are n1,n2,… and r1,r2,…; relationship "
        "from/to reference node temp_ids. Omit anything that doesn't fit the allowed "
        "categories/types.\n\n"
        f"TEXT:\n{text}"
    )


def _normalize(raw, categories, edge_types, existing_by_uid=None, searchable=None):
    """Map the LLM JSON to the proposal contract; drop entries with unknown slugs.

    A node carrying a valid ``existing_uid`` (one offered in the prompt) becomes an
    ``existing`` node linked to that graph uid; otherwise it's a new node, whose
    category-identifier property (``searchable``) is filled from the display name so
    required fields (e.g. ``title``) aren't left empty.
    """
    existing_by_uid = existing_by_uid or {}
    searchable = searchable or {}
    nodes, valid_temp = [], set()
    for i, n in enumerate(raw.get("nodes") or [], start=1):
        temp_id = n.get("temp_id") or f"n{i}"
        existing_uid = n.get("existing_uid")
        if existing_uid and existing_uid in existing_by_uid:
            info = existing_by_uid[existing_uid]
            slug = info["category_slug"]
            valid_temp.add(temp_id)
            nodes.append({
                "temp_id": temp_id, "kind": "existing", "category_slug": slug,
                "category_name": categories.get(slug, slug), "uid": existing_uid,
                "display": info["display"], "properties": {},
                "evidence": [],
                "match": {"method": "exact", "matched_uid": existing_uid, "score": 1.0, "candidates": []},
                "duplicate_warning": False, "merge_into_uid": None, "ner_label": None,
                "confidence": 1.0, "approval": "pending", "validation": {},
            })
            continue
        slug = n.get("category_slug")
        if slug not in categories:
            continue
        valid_temp.add(temp_id)
        props = dict(n.get("properties") or {})
        display = n.get("display") or props.get("name") or props.get("title") or temp_id
        # Ensure the category's identifier property is populated, so required
        # fields aren't left empty (the cause of "'title' is required" errors).
        ident = searchable.get(slug) or "name"
        if not props.get(ident):
            props[ident] = display
        nodes.append({
            "temp_id": temp_id, "kind": "new", "category_slug": slug,
            "category_name": categories[slug], "uid": None, "display": display,
            "properties": props,
            "evidence": [], "match": None, "duplicate_warning": False,
            "merge_into_uid": None, "ner_label": None, "confidence": 0.6,
            "approval": "pending", "validation": {},
        })

    relationships = []
    for i, r in enumerate(raw.get("relationships") or [], start=1):
        slug = r.get("edge_type_slug")
        if slug not in edge_types or r.get("from") not in valid_temp or r.get("to") not in valid_temp:
            continue
        relationships.append({
            "temp_id": r.get("temp_id") or f"r{i}", "edge_type_slug": slug,
            "rel_type": edge_types[slug]["rel_type"], "edge_type_name": edge_types[slug]["name"],
            "from": r["from"], "to": r["to"], "properties": r.get("properties") or {},
            "evidence": [], "trigger_matched": False, "confidence": 0.6,
            "approval": "pending", "validation": {},
        })
    return {"nodes": nodes, "relationships": relationships}


@IngestStrategy.register
class LLMStrategy(IngestStrategy):
    key = "llm"
    label = "LLM extraction (OpenAI)"
    description = "GPT extracts entities & relationships into a reviewable proposal."
    mode = MODE_REVIEW

    def is_available(self) -> bool:
        # Targets OpenAI cloud — needs an API key to be usable.
        return bool((getattr(settings, "OPENAI_API_KEY", "") or "").strip())

    def run(self, text, user=None, *, include_existing_nodes=False):
        proposal = self._extract(text, include_existing_nodes=include_existing_nodes)
        validation.revalidate(proposal)
        summary = validation.summarize(proposal)
        summary["strategy"] = self.key
        return persist_review(text=text, proposal=proposal, summary=summary, user=user)

    def _extract(self, text, include_existing_nodes=False) -> dict:
        api_key = (getattr(settings, "OPENAI_API_KEY", "") or "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set for the 'llm' ingest strategy.")
        from openai import OpenAI

        categories, edge_types, searchable = _bento_schema()
        existing_nodes = existing_nodes_in_text(text) if include_existing_nodes else []
        existing_by_uid = {n["uid"]: n for n in existing_nodes}
        model = getattr(settings, "INGESTOR_LLM_MODEL", "") or "gpt-4.1-mini"
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You extract knowledge graphs as strict JSON."},
                {"role": "user", "content": _prompt(text, categories, edge_types, existing_nodes)},
            ],
        )
        raw = json.loads(resp.choices[0].message.content or "{}")
        return _normalize(raw, categories, edge_types, existing_by_uid, searchable)
