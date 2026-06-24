"""Ingestion strategy registry (strategy pattern).

Each strategy turns pasted text into an :class:`IngestProposal`:
  - ``mode == "review"``   → builds a proposal (status READY) for the existing
    Cytoscape review + Bento-apply flow (human in the loop).
  - ``mode == "autobuild"`` → writes the graph directly, then records an APPLIED
    proposal for audit (no review).

The deterministic spaCy/rapidfuzz pipeline is preserved as the default ``review``
strategy; new strategies (LLM extraction, neo4j-graphrag KG-builder) register here.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import ClassVar

logger = logging.getLogger(__name__)

MODE_REVIEW = "review"
MODE_AUTOBUILD = "autobuild"


class IngestStrategy(ABC):
    registry: ClassVar[dict[str, "IngestStrategy"]] = {}

    key: ClassVar[str] = ""
    label: ClassVar[str] = ""
    description: ClassVar[str] = ""
    mode: ClassVar[str] = MODE_REVIEW

    @classmethod
    def register(cls, strategy_cls):
        instance = strategy_cls()
        if not instance.key:
            raise ValueError(f"{strategy_cls.__name__} must define a non-empty key")
        cls.registry[instance.key] = instance
        return strategy_cls

    @classmethod
    def get(cls, key: str) -> "IngestStrategy | None":
        return cls.registry.get(key)

    @classmethod
    def all(cls) -> list["IngestStrategy"]:
        return list(cls.registry.values())

    def is_available(self) -> bool:
        """Whether this strategy's backend (LLM / embeddings) is configured.

        Unavailable strategies are hidden from the UI selector and rejected by
        the generate endpoint, so a user never picks one that can only fail with
        a connection error. Default: always available — the deterministic
        spaCy/rapidfuzz pipeline has no network backend.
        """
        return True

    @classmethod
    def choices(cls, *, available_only: bool = False) -> list[dict]:
        return [
            {"key": s.key, "label": s.label, "description": s.description, "mode": s.mode}
            for s in cls.registry.values()
            if not available_only or s.is_available()
        ]

    @abstractmethod
    def run(self, text: str, user=None):
        """Return an :class:`IngestProposal` (see module docstring for modes)."""
        raise NotImplementedError


def _owner(user):
    return user if getattr(user, "is_authenticated", False) else None


def persist_review(*, text, proposal, summary, user):
    """Persist a review-ready proposal (status READY)."""
    from ...models import IngestProposal

    return IngestProposal.objects.create(
        status=IngestProposal.STATUS_READY,
        source_text=text,
        proposal=proposal or {},
        summary=summary or {},
        created_by=_owner(user),
    )


def persist_applied(*, text, proposal, summary, apply_result, user):
    """Persist an already-applied proposal (status APPLIED) for audit/autobuild."""
    from django.utils import timezone

    from ...models import IngestProposal

    return IngestProposal.objects.create(
        status=IngestProposal.STATUS_APPLIED,
        source_text=text,
        proposal=proposal or {},
        summary=summary or {},
        apply_result=apply_result or {},
        applied_at=timezone.now(),
        created_by=_owner(user),
    )
