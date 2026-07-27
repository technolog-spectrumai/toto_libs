"""The original deterministic spaCy/rapidfuzz pipeline, preserved as a strategy."""
from __future__ import annotations

from ..pipeline import build_proposal_dict
from .base import MODE_REVIEW, IngestStrategy, persist_review


@IngestStrategy.register
class DeterministicStrategy(IngestStrategy):
    key = "deterministic"
    label = "Deterministic (spaCy + RapidFuzz)"
    description = "Rule-based entity & relationship detection. No LLM — fast and predictable."
    mode = MODE_REVIEW

    def run(self, text, user=None, *, include_existing_nodes=False):
        proposal, summary = build_proposal_dict(text, include_existing_nodes=include_existing_nodes)
        return persist_review(text=text, proposal=proposal, summary=summary, user=user)
