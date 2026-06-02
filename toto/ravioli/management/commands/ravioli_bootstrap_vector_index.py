"""Create the Neo4j vector index for toto chunk embeddings.

Idempotent — safe to run multiple times. Requires embeddings to be enabled
(VICUNA_EMBEDDINGS_ENABLED=True) so the dimension can be determined.
"""
from __future__ import annotations

import re

from django.core.management.base import BaseCommand


def _safe_id(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or ""))


class Command(BaseCommand):
    help = (
        "Create the Neo4j vector index for toto chunk embeddings (idempotent). "
        "Requires VICUNA_EMBEDDINGS_ENABLED=True and a running Ollama instance."
    )

    def handle(self, *args, **options):
        from django.conf import settings

        from toto.vicuna.embeddings import EmbeddingUnavailable, embedding_dimension

        # --- determine embedding dimension --------------------------------
        try:
            dim = embedding_dimension()
        except EmbeddingUnavailable as exc:
            self.stderr.write(
                self.style.WARNING(
                    f"Embeddings unavailable — cannot determine vector dimension: {exc}\n"
                    "Set VICUNA_EMBEDDINGS_ENABLED=True and ensure Ollama is running, then retry."
                )
            )
            raise SystemExit(1)

        # --- read index config from settings ------------------------------
        index_name = getattr(settings, "TOTO_NEO4J_VECTOR_INDEX", "toto_chunk_embeddings")
        label = getattr(settings, "TOTO_VECTOR_NODE_LABEL", "TotoChunk")
        embedding_prop = getattr(settings, "TOTO_VECTOR_EMBEDDING_PROPERTY", "embedding")

        for setting_name, value in [
            ("TOTO_VECTOR_NODE_LABEL", label),
            ("TOTO_VECTOR_EMBEDDING_PROPERTY", embedding_prop),
            ("TOTO_NEO4J_VECTOR_INDEX", index_name),
        ]:
            if not _safe_id(value):
                self.stderr.write(
                    self.style.ERROR(
                        f"{setting_name}={value!r} is not a safe Neo4j identifier."
                    )
                )
                raise SystemExit(1)

        self.stdout.write(
            f"Creating vector index {index_name!r} "
            f"on (:{label}).{embedding_prop} dim={dim} …"
        )

        # --- connect and create index (IF NOT EXISTS = idempotent) --------
        from toto.ravioli.connection import Neo4jClient

        client = Neo4jClient()
        try:
            client.run_cypher(
                f"CREATE VECTOR INDEX {index_name} IF NOT EXISTS "
                f"FOR (n:{label}) ON (n.{embedding_prop}) "
                f"OPTIONS {{indexConfig: {{"
                f"`vector.dimensions`: {int(dim)}, "
                f"`vector.similarity_function`: 'cosine'"
                f"}}}}"
            )
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Failed to create vector index: {exc}"))
            raise SystemExit(1)
        finally:
            client.close()

        self.stdout.write(
            self.style.SUCCESS(f"Vector index {index_name!r} is ready (dim={dim}).")
        )
