"""GraphRAG for sabbia — retrieve context from the ravioli Neo4j graph and
answer via the official ``neo4j-graphrag`` framework.

Boundary: this is ravioli's module (ravioli owns Neo4j). sabbia lazy-imports
``run_graphrag`` and passes in a framework LLM built from the agent's connector +
gervazy vault. Everything is gated on ``RAVIOLI_ENABLED`` and fails soft — any
failure returns ``("", "")`` so the caller falls back to the plain chat endpoint.

MVP note: ``Text2CypherRetriever`` (LLM → Cypher) is used directly, WITHOUT a
read-only guard — see the plan's "Future hardening". Do not assume the retrieval
path is non-mutable.
"""
from __future__ import annotations

import logging

from django.conf import settings

from neo4j_graphrag.embeddings.base import Embedder
from neo4j_graphrag.generation import GraphRAG
from neo4j_graphrag.retrievers import Text2CypherRetriever, VectorCypherRetriever
from neo4j_graphrag.retrievers.base import Retriever
from neo4j_graphrag.types import RawSearchResult

logger = logging.getLogger(__name__)

# 1-hop expansion around each vector-matched node, folded into the record so the
# default formatter (str(record)) carries both the chunk text and its relations.
_RETRIEVAL_QUERY = """
OPTIONAL MATCH (node)-[r]-(m)
WITH node, score,
     collect(DISTINCT type(r) + ' -> ' + coalesce(m.name, m.title, m.uid, ''))[..20] AS rels
RETURN coalesce(node.text, node.name, node.title, '') AS text, rels, score
"""


def _index_name() -> str:
    return getattr(settings, "TOTO_NEO4J_VECTOR_INDEX", "toto_chunk_embeddings")


class VicunaEmbedder(Embedder):
    """Embed via toto.vicuna (the sole embedding owner) so query vectors use the
    same model the ``toto_chunk_embeddings`` index was built with."""

    def embed_query(self, text: str) -> list[float]:
        from toto.vicuna.embeddings import embed_text

        return embed_text(text)


class CompositeRetriever(Retriever):
    """Union of a ``VectorCypherRetriever`` (semantic seed + 1-hop graph
    expansion) and an optional ``Text2CypherRetriever`` (NL → Cypher). Each
    sub-retriever's failure is isolated so the other still contributes."""

    VERIFY_NEO4J_VERSION = False  # inner retrievers validate; skip the extra round-trip

    def __init__(self, driver, *, vector_cypher, text2cypher=None, neo4j_database=None):
        super().__init__(driver, neo4j_database)
        self._vector_cypher = vector_cypher
        self._text2cypher = text2cypher
        self.last_errors: list[str] = []  # per-call sub-retriever failures (for diagnostics)

    def get_search_results(self, query_text: str = "", top_k: int = 5, **kwargs) -> RawSearchResult:
        records = []
        self.last_errors = []
        try:
            vc = self._vector_cypher.get_search_results(query_text=query_text, top_k=top_k)
            records.extend(vc.records)
        except Exception as exc:  # noqa: BLE001 — isolate vector failures
            logger.warning("GraphRAG vector retrieval failed: %s", exc)
            self.last_errors.append(f"vector: {exc}")
        if self._text2cypher is not None:
            try:
                t2c = self._text2cypher.get_search_results(query_text=query_text)
                records.extend(t2c.records)
            except Exception as exc:  # noqa: BLE001 — isolate text2cypher failures
                logger.warning("GraphRAG text2cypher retrieval failed: %s", exc)
                self.last_errors.append(f"text2cypher: {exc}")
        return RawSearchResult(records=records, metadata={"__retriever": "CompositeRetriever"})


def build_retriever(driver, *, llm=None, text2cypher=True) -> CompositeRetriever:
    """Compose the read retriever. ``llm`` (a neo4j-graphrag LLMInterface) is only
    needed when ``text2cypher`` is on."""
    vector_cypher = VectorCypherRetriever(
        driver,
        index_name=_index_name(),
        retrieval_query=_RETRIEVAL_QUERY,
        embedder=VicunaEmbedder(),
    )
    t2c = None
    if text2cypher and llm is not None:
        # MVP: used directly, no read-only enforcement (see module docstring).
        t2c = Text2CypherRetriever(driver, llm=llm)
    return CompositeRetriever(driver, vector_cypher=vector_cypher, text2cypher=t2c)


def run_graphrag(*, llm, query_text, top_k=5, text2cypher=True, return_cause=False):
    """Answer ``query_text`` grounded in the ravioli graph via neo4j-graphrag.

    Returns ``(answer, context)`` — or ``(answer, context, cause)`` when
    ``return_cause`` is True, where ``cause`` explains an empty answer (LLM
    unreachable, sub-retriever errors, no matching data). Returns empty on any
    failure (RAVIOLI disabled, empty query, exception) so chat callers fall back
    to the plain endpoint; the explicit "Ask AI" task uses ``return_cause`` to
    report *why* instead of a generic message.
    """
    from toto.ravioli.connection import Neo4jClient, is_enabled

    def _ret(answer="", context="", cause=""):
        return (answer, context, cause) if return_cause else (answer, context)

    if not is_enabled():
        return _ret(cause="RAVIOLI_ENABLED is False — graph is unavailable.")
    if not (query_text or "").strip():
        return _ret(cause="empty query.")

    client = None
    try:
        client = Neo4jClient()
        driver = client.driver()
        retriever = build_retriever(driver, llm=llm, text2cypher=text2cypher)
        rag = GraphRAG(retriever=retriever, llm=llm)
        resp = rag.search(
            query_text=query_text,
            retriever_config={"top_k": int(top_k)},
            return_context=True,
        )
        answer = (getattr(resp, "answer", "") or "").strip()
        ctx_items = getattr(getattr(resp, "retriever_result", None), "items", None) or []
        context = "\n".join(i.content for i in ctx_items if getattr(i, "content", ""))
        if answer:
            return _ret(answer, context)
        cause = "; ".join(getattr(retriever, "last_errors", []) or []) or (
            "retrieval returned no records and the model produced no answer "
            "(the graph may have no data matching the question)."
        )
        return _ret("", context, cause)
    except Exception as exc:  # noqa: BLE001 — never break chat
        logger.warning("GraphRAG run failed: %s", exc)
        return _ret(cause=f"{type(exc).__name__}: {exc}")
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
