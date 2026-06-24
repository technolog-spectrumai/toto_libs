"""(Re)embed graph nodes and (re)build the Neo4j vector index.

The bootstrap command only *creates* the index — nothing populates node embeddings,
so semantic search / GraphRAG return nothing until this runs. This command:
  1. (optionally) drops the index,
  2. creates it sized to the ACTIVE embedding provider's dimension,
  3. embeds the text of matching nodes and writes the embedding property,
so ``db.index.vector.queryNodes`` actually returns results.

Switching ``VICUNA_EMBEDDING_PROVIDER`` (e.g. ollama → openai) changes the vector
dimension — re-run this with ``--drop`` to rebuild + re-embed.

Usage:
  VICUNA_EMBEDDINGS_ENABLED=1 python manage.py ravioli_reindex_embeddings --drop
  python manage.py ravioli_reindex_embeddings --label Idea --text-property name
"""
from __future__ import annotations

import re

from django.conf import settings
from django.core.management.base import BaseCommand


def _safe_id(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or ""))


class Command(BaseCommand):
    help = "Embed node text and (re)build the Neo4j vector index for the active provider."

    def add_arguments(self, parser):
        parser.add_argument("--label", default=None, help="Node label to embed (default TOTO_VECTOR_NODE_LABEL).")
        parser.add_argument("--text-property", default=None, help="Property holding the text (default TOTO_VECTOR_TEXT_PROPERTY).")
        parser.add_argument("--embedding-property", default=None, help="Property to store the vector (default TOTO_VECTOR_EMBEDDING_PROPERTY).")
        parser.add_argument("--index", default=None, help="Vector index name (default TOTO_NEO4J_VECTOR_INDEX).")
        parser.add_argument("--drop", action="store_true", help="Drop the index first (required when the dimension changed).")
        parser.add_argument("--batch", type=int, default=100, help="Embedding batch size.")
        parser.add_argument("--limit", type=int, default=0, help="Cap nodes processed (0 = all).")

    def handle(self, *args, **options):
        from toto.vicuna.embeddings import EmbeddingUnavailable, embed_texts, embedding_dimension

        label = options["label"] or getattr(settings, "TOTO_VECTOR_NODE_LABEL", "TotoChunk")
        text_prop = options["text_property"] or getattr(settings, "TOTO_VECTOR_TEXT_PROPERTY", "text")
        emb_prop = options["embedding_property"] or getattr(settings, "TOTO_VECTOR_EMBEDDING_PROPERTY", "embedding")
        index_name = options["index"] or getattr(settings, "TOTO_NEO4J_VECTOR_INDEX", "toto_chunk_embeddings")

        for name, value in [("label", label), ("text-property", text_prop),
                            ("embedding-property", emb_prop), ("index", index_name)]:
            if not _safe_id(value):
                self.stderr.write(self.style.ERROR(f"{name}={value!r} is not a safe Neo4j identifier."))
                raise SystemExit(1)

        # --- dimension from the active provider --------------------------------
        try:
            dim = embedding_dimension()
        except EmbeddingUnavailable as exc:
            self.stderr.write(self.style.WARNING(
                f"Embeddings unavailable: {exc}\nSet VICUNA_EMBEDDINGS_ENABLED=1 and configure the provider."
            ))
            raise SystemExit(1)

        from toto.ravioli.connection import Neo4jClient

        client = Neo4jClient()
        try:
            if options["drop"]:
                client.run_cypher(f"DROP INDEX {index_name} IF EXISTS")
                self.stdout.write(f"Dropped index {index_name!r}.")
            client.run_cypher(
                f"CREATE VECTOR INDEX {index_name} IF NOT EXISTS "
                f"FOR (n:{label}) ON (n.{emb_prop}) "
                f"OPTIONS {{indexConfig: {{`vector.dimensions`: {int(dim)}, "
                f"`vector.similarity_function`: 'cosine'}}}}"
            )
            self.stdout.write(f"Vector index {index_name!r} on (:{label}).{emb_prop} dim={dim} ready.")

            rows = client.run_cypher(
                f"MATCH (n:{label}) WHERE n.{text_prop} IS NOT NULL AND trim(toString(n.{text_prop})) <> '' "
                f"RETURN elementId(n) AS id, toString(n.{text_prop}) AS text"
                + (f" LIMIT {int(options['limit'])}" if options["limit"] else "")
            )
            total = len(rows)
            self.stdout.write(f"Embedding {total} node(s)…")

            batch = max(1, int(options["batch"]))
            done = 0
            for start in range(0, total, batch):
                chunk = rows[start:start + batch]
                vectors = embed_texts([r["text"] for r in chunk])
                payload = [{"id": r["id"], "vec": v} for r, v in zip(chunk, vectors)]
                client.run_cypher(
                    f"UNWIND $rows AS row MATCH (n) WHERE elementId(n) = row.id "
                    f"SET n.{emb_prop} = row.vec",
                    {"rows": payload},
                )
                done += len(chunk)
                self.stdout.write(f"  {done}/{total}")

            self.stdout.write(self.style.SUCCESS(
                f"Re-embedded {done} node(s) into {index_name!r} (dim={dim})."
            ))
        finally:
            client.close()
