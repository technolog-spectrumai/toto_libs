"""Ravioli vector search — queries the Neo4j vector index using Steven embeddings.

Ravioli owns the Neo4j query; Steven owns the embedding call.
"""
from __future__ import annotations

import re

from django.conf import settings

from toto.ravioli.connection import Neo4jClient, Neo4jConnectionError
from toto.steven.services.embeddings import EmbeddingUnavailable, embed_text


class VectorSearchUnavailable(RuntimeError):
    """Raised when the vector search cannot complete for any reason."""


def _safe_id(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or ""))


def retrieve_vector_context(query: str, top_k: int = 5) -> str:
    """Embed *query* via Steven, search the Neo4j vector index, return plain-text
    context suitable for injection into an LLM system prompt.

    Raises VectorSearchUnavailable when embeddings or Neo4j are unreachable.
    Does not call an LLM itself.
    """
    try:
        query_vector = embed_text(query)
    except EmbeddingUnavailable as exc:
        raise VectorSearchUnavailable(f"Embedding unavailable: {exc}") from exc

    index_name = getattr(settings, "TOTO_NEO4J_VECTOR_INDEX", "toto_chunk_embeddings")
    text_prop = getattr(settings, "TOTO_VECTOR_TEXT_PROPERTY", "text")

    if not _safe_id(text_prop):
        raise VectorSearchUnavailable(
            f"TOTO_VECTOR_TEXT_PROPERTY {text_prop!r} is not a safe Neo4j identifier."
        )

    client = Neo4jClient()
    try:
        records = client.run_cypher(
            f"CALL db.index.vector.queryNodes($index, $k, $vector) "
            f"YIELD node, score "
            f"RETURN node.{text_prop} AS text, score "
            f"ORDER BY score DESC",
            {"index": index_name, "k": int(top_k), "vector": query_vector},
        )
    except Neo4jConnectionError as exc:
        raise VectorSearchUnavailable(f"Neo4j unreachable: {exc}") from exc
    except Exception as exc:
        raise VectorSearchUnavailable(f"Vector query failed: {exc}") from exc
    finally:
        client.close()

    hits = []
    for record in records:
        text = record.get("text") or ""
        score = record.get("score")
        if text:
            score_str = f" (score={score:.4f})" if score is not None else ""
            hits.append(f"- {text.strip()}{score_str}")

    if not hits:
        return ""

    lines = [
        "Vector search context from Toto's Neo4j embedding index.",
        "Use this context only when relevant.",
        "",
        *hits,
    ]
    return "\n".join(lines)
