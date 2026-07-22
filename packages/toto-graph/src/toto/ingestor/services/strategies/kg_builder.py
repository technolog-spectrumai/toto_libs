"""neo4j-graphrag KG-builder strategy (autobuild) — LLM extracts AND writes the
graph directly, with no review step.

⚠️ Bypasses the Cytoscape review AND Bento template validation. Extraction is
best-effort constrained by the Bento labels/rel-types passed as the schema, but
the written nodes/edges may not follow Bento's uid/property conventions. Requires
embeddings enabled (SimpleKGPipeline embeds chunks).
"""
from __future__ import annotations

import logging

from django.conf import settings

from .base import MODE_AUTOBUILD, IngestStrategy, existing_nodes_in_text, persist_applied

logger = logging.getLogger(__name__)


@IngestStrategy.register
class KGBuilderStrategy(IngestStrategy):
    key = "kg-builder"
    label = "Auto-builder (neo4j-graphrag)"
    description = "LLM extracts and writes nodes/edges to the graph directly — no review."
    mode = MODE_AUTOBUILD

    def is_available(self) -> bool:
        # Needs OpenAI (entity extraction) AND the local vicuna/ollama embeddings
        # backend (SimpleKGPipeline embeds chunks via VicunaEmbedder). Hidden when
        # either is missing so users don't hit an opaque connection error.
        if not (getattr(settings, "OPENAI_API_KEY", "") or "").strip():
            return False
        try:
            from toto.vicuna.embeddings import embeddings_enabled
        except Exception:  # noqa: BLE001 — vicuna app not installed
            return False
        return bool(embeddings_enabled())

    def run(self, text, user=None, *, include_existing_nodes=False):
        from asgiref.sync import async_to_sync
        from neo4j_graphrag.experimental.pipeline.kg_builder import SimpleKGPipeline
        from neo4j_graphrag.llm import OpenAILLM

        from toto.ravioli.connection import Neo4jClient
        from toto.ravioli.rag import VicunaEmbedder

        api_key = (getattr(settings, "OPENAI_API_KEY", "") or "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set for the 'kg-builder' strategy.")

        # SimpleKGPipeline owns its own extraction prompt, so the only hook for
        # "include existing nodes" is to feed them as context via the input text
        # (best-effort; entity resolution then merges matches into existing nodes).
        run_text = text
        if include_existing_nodes:
            existing = existing_nodes_in_text(text)
            if existing:
                listing = "; ".join(f"{n['display']} ({n['category_slug']})" for n in existing)
                run_text = f"Known existing entities (link to these where relevant): {listing}.\n\n{text}"

        llm = OpenAILLM(
            model_name=getattr(settings, "INGESTOR_LLM_MODEL", "") or "gpt-4.1-mini",
            api_key=api_key,
            model_params={"temperature": 0},
        )
        entities, relations = self._bento_schema()

        client = Neo4jClient()
        try:
            pipeline = SimpleKGPipeline(
                llm=llm,
                driver=client.driver(),
                embedder=VicunaEmbedder(),
                entities=entities or None,
                relations=relations or None,
                from_pdf=False,
                perform_entity_resolution=True,
            )
            result = async_to_sync(pipeline.run_async)(text=run_text)
        finally:
            client.close()

        summary = {"strategy": self.key, "result": str(result)}
        return persist_applied(
            text=text, proposal={}, summary=summary,
            apply_result={"kg_builder": str(result)}, user=user,
        )

    def _bento_schema(self):
        """Constrain extraction to the Bento node labels + relationship types."""
        from toto.bento.models import BentoCategory, BentoEdgeType

        entities = [c.neo4j_label for c in BentoCategory.objects.filter(internal=False) if c.neo4j_label]
        relations = [e.rel_type for e in BentoEdgeType.objects.all() if e.rel_type]
        return entities, relations
