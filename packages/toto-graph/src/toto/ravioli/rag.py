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


def _node_display(props: dict) -> str:
    for key in ("name", "title", "label"):
        if props.get(key):
            return str(props[key])
    return (str(props.get("uid") or "")[:8]) or "node"


def _extract_graph(records) -> dict:
    """Pull neo4j Nodes/Relationships/Paths out of retriever records into a
    Cytoscape-ready ``{"nodes": [...], "edges": [...]}`` (deduped by element_id).

    Duck-typed (Node has ``labels``; Relationship has ``type``/``start_node``;
    Path has ``nodes``/``relationships``) to avoid a hard neo4j import here.
    """
    nodes: dict = {}
    edges: dict = {}

    def add(value):
        if value is None:
            return
        if hasattr(value, "labels") and hasattr(value, "element_id"):  # Node
            eid = value.element_id
            if eid not in nodes:
                props = dict(value)
                nodes[eid] = {
                    "id": eid,
                    "label": _node_display(props),
                    "category": next(iter(value.labels), "") if value.labels else "",
                    "properties": props,
                }
        elif hasattr(value, "type") and hasattr(value, "start_node") and hasattr(value, "end_node"):  # Rel
            add(value.start_node)
            add(value.end_node)
            eid = getattr(value, "element_id", None) or (
                f"{value.start_node.element_id}-{value.type}-{value.end_node.element_id}"
            )
            if eid not in edges:
                edges[eid] = {
                    "id": eid,
                    "source": value.start_node.element_id,
                    "target": value.end_node.element_id,
                    "label": value.type,
                }
        elif hasattr(value, "nodes") and hasattr(value, "relationships"):  # Path
            for n in value.nodes:
                add(n)
            for r in value.relationships:
                add(r)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                add(item)
        elif isinstance(value, dict):
            for item in value.values():
                add(item)

    for rec in records or []:
        try:
            values = rec.values() if hasattr(rec, "values") else list(rec)
        except Exception:  # noqa: BLE001 — be tolerant of odd record shapes
            values = []
        for v in values:
            add(v)
    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


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
        self.last_graph: dict = {"nodes": [], "edges": []}  # per-call retrieved subgraph

    def get_search_results(self, query_text: str = "", top_k: int = 5, **kwargs) -> RawSearchResult:
        records = []
        self.last_errors = []
        if self._vector_cypher is not None:
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
        self.last_graph = _extract_graph(records)
        return RawSearchResult(records=records, metadata={"__retriever": "CompositeRetriever"})


def build_retriever(driver, *, llm=None, text2cypher=True) -> CompositeRetriever:
    """Compose the read retriever. ``llm`` (a neo4j-graphrag LLMInterface) is only
    needed when ``text2cypher`` is on.

    The vector sub-retriever is **best-effort**: ``VectorCypherRetriever`` validates
    its index at construction, so when the index is missing/misconfigured (or
    embeddings are off) we skip it and answer via Text2Cypher alone, rather than
    letting a missing index abort the whole pipeline.
    """
    vector_cypher = None
    try:
        vector_cypher = VectorCypherRetriever(
            driver,
            index_name=_index_name(),
            retrieval_query=_RETRIEVAL_QUERY,
            embedder=VicunaEmbedder(),
        )
    except Exception as exc:  # noqa: BLE001 — no vector index / embeddings off → Text2Cypher only
        logger.warning("GraphRAG vector retriever unavailable (%s) — using Text2Cypher only.", exc)
    t2c = None
    if text2cypher and llm is not None:
        # MVP: used directly, no read-only enforcement (see module docstring).
        t2c = Text2CypherRetriever(driver, llm=llm)
    return CompositeRetriever(driver, vector_cypher=vector_cypher, text2cypher=t2c)


def run_graphrag(*, llm, query_text, top_k=5, text2cypher=True, detail=False):
    """Answer ``query_text`` grounded in the ravioli graph via neo4j-graphrag.

    Returns ``(answer, context)`` — or, when ``detail`` is True, a dict
    ``{"answer", "context", "cause", "graph"}`` where ``cause`` explains an empty
    answer and ``graph`` is the retrieved subgraph (``{"nodes", "edges"}``, for
    Cytoscape). Empty on any failure (RAVIOLI disabled, empty query, exception) so
    chat callers fall back to the plain endpoint; the "Ask AI" task uses ``detail``
    to surface the cause and render the context as a graph.
    """
    from toto.ravioli.connection import Neo4jClient, is_enabled

    def _ret(answer="", context="", cause="", graph=None):
        if detail:
            return {
                "answer": answer, "context": context, "cause": cause,
                "graph": graph or {"nodes": [], "edges": []},
            }
        return answer, context

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
        graph = getattr(retriever, "last_graph", None)
        if answer:
            return _ret(answer, context, graph=graph)
        cause = "; ".join(getattr(retriever, "last_errors", []) or []) or (
            "retrieval returned no records and the model produced no answer "
            "(the graph may have no data matching the question)."
        )
        return _ret("", context, cause, graph=graph)
    except Exception as exc:  # noqa: BLE001 — never break chat
        logger.warning("GraphRAG run failed: %s", exc)
        return _ret(cause=f"{type(exc).__name__}: {exc}")
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
