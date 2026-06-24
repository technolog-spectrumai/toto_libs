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
from .base import MODE_REVIEW, IngestStrategy, persist_review

logger = logging.getLogger(__name__)


def _bento_schema():
    """Return (categories, edge_types) metadata for the prompt + slug lookups."""
    from toto.bento.models import BentoCategory, BentoEdgeType

    categories = {c.slug: c.name for c in BentoCategory.objects.all()}
    edge_types = {}
    for et in BentoEdgeType.objects.prefetch_related("allowed_sources", "allowed_targets"):
        edge_types[et.slug] = {
            "name": et.name,
            "rel_type": et.rel_type,
            "sources": list(et.allowed_sources.values_list("slug", flat=True)),
            "targets": list(et.allowed_targets.values_list("slug", flat=True)),
        }
    return categories, edge_types


def _prompt(text, categories, edge_types):
    cats = "\n".join(f"  - {slug}: {name}" for slug, name in categories.items())
    edges = "\n".join(
        f"  - {slug}: {e['name']} (from {e['sources'] or 'any'} to {e['targets'] or 'any'})"
        for slug, e in edge_types.items()
    )
    return (
        "Extract a knowledge graph from the text below. Use ONLY these node "
        f"categories (by slug):\n{cats}\n\nand ONLY these relationship types (by slug):\n{edges}\n\n"
        "Return STRICT JSON: {\"nodes\": [{\"temp_id\": \"n1\", \"category_slug\": \"...\", "
        "\"display\": \"...\", \"properties\": {\"name\": \"...\"}}], \"relationships\": "
        "[{\"temp_id\": \"r1\", \"edge_type_slug\": \"...\", \"from\": \"n1\", \"to\": \"n2\", "
        "\"properties\": {}}]}. temp_ids are n1,n2,… and r1,r2,…; relationship from/to reference "
        "node temp_ids. Omit anything that doesn't fit the allowed categories/types.\n\n"
        f"TEXT:\n{text}"
    )


def _normalize(raw, categories, edge_types):
    """Map the LLM JSON to the proposal contract; drop entries with unknown slugs."""
    nodes, valid_temp = [], set()
    for i, n in enumerate(raw.get("nodes") or [], start=1):
        slug = n.get("category_slug")
        if slug not in categories:
            continue
        temp_id = n.get("temp_id") or f"n{i}"
        valid_temp.add(temp_id)
        display = n.get("display") or (n.get("properties") or {}).get("name") or temp_id
        nodes.append({
            "temp_id": temp_id, "kind": "new", "category_slug": slug,
            "category_name": categories[slug], "uid": None, "display": display,
            "properties": n.get("properties") or {"name": display},
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

    def run(self, text, user=None):
        proposal = self._extract(text)
        validation.revalidate(proposal)
        summary = validation.summarize(proposal)
        summary["strategy"] = self.key
        return persist_review(text=text, proposal=proposal, summary=summary, user=user)

    def _extract(self, text) -> dict:
        api_key = (getattr(settings, "OPENAI_API_KEY", "") or "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set for the 'llm' ingest strategy.")
        from openai import OpenAI

        categories, edge_types = _bento_schema()
        model = getattr(settings, "INGESTOR_LLM_MODEL", "") or "gpt-4.1-mini"
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You extract knowledge graphs as strict JSON."},
                {"role": "user", "content": _prompt(text, categories, edge_types)},
            ],
        )
        raw = json.loads(resp.choices[0].message.content or "{}")
        return _normalize(raw, categories, edge_types)
