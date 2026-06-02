"""Ravioli vector search — queries the Neo4j vector index using Steven embeddings.

Ravioli owns the Neo4j query; Steven owns the embedding call.
"""
from __future__ import annotations

import re

from django.conf import settings

from toto.ravioli.connection import Neo4jClient, Neo4jConnectionError
from toto.vicuna.embeddings import EmbeddingUnavailable, embed_text


class VectorSearchUnavailable(RuntimeError):
    """Raised when the vector search cannot complete for any reason."""


def _safe_id(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or ""))


def _to_json_safe(v):
    """Convert Neo4j driver values to JSON-serializable Python types."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, list):
        return [_to_json_safe(i) for i in v]
    if isinstance(v, dict):
        return {k: _to_json_safe(val) for k, val in v.items()}
    return str(v)


def _shared_vector_query(query: str, top_k: int) -> list:
    """Embed *query* and run the vector index query. Returns raw Neo4j records.

    Raises VectorSearchUnavailable on any failure.
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
            "CALL db.index.vector.queryNodes($index, $k, $vector) "
            "YIELD node, score "
            f"RETURN labels(node) AS labels, properties(node) AS props, "
            f"node.{text_prop} AS text, score "
            "ORDER BY score DESC",
            {"index": index_name, "k": int(top_k), "vector": query_vector},
        )
    except Neo4jConnectionError as exc:
        raise VectorSearchUnavailable(f"Neo4j unreachable: {exc}") from exc
    except Exception as exc:
        raise VectorSearchUnavailable(f"Vector query failed: {exc}") from exc
    finally:
        client.close()

    return records


def retrieve_vector_results(query: str, top_k: int = 25) -> list[dict]:
    """Embed *query* and return structured search results from the Neo4j vector index.

    Each result: ``{"labels": [...], "props": {...}, "score": float}``.
    Raises VectorSearchUnavailable on any failure.
    """
    records = _shared_vector_query(query, top_k)
    results = []
    for record in records:
        raw_props = record.get("props") or {}
        props = {k: _to_json_safe(v) for k, v in raw_props.items()}
        score = record.get("score")
        results.append({
            "labels": list(record.get("labels") or []),
            "props": props,
            "score": round(float(score), 4) if score is not None else None,
        })
    return results


def retrieve_vector_context(query: str, top_k: int = 5) -> str:
    """Embed *query* via Steven, return plain-text context for LLM injection.

    Raises VectorSearchUnavailable when embeddings or Neo4j are unreachable.
    Does not call an LLM itself.
    """
    records = _shared_vector_query(query, top_k)

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
