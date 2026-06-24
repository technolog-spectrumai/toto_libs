from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib.util import find_spec
from typing import Any

from django.conf import settings


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "i", "in", "is", "it", "me", "of", "on", "or", "that", "the", "this",
    "to", "what", "when", "where", "which", "who", "why", "with", "you",
}

META_PROPS = {
    "ravioli_owned",
    "ravioli_label",
    "ravioli_from_label",
    "ravioli_to_label",
    "ravioli_uuid",
}


READ_ONLY_CYPHER_TEMPLATE = """
Task: Generate a read-only Cypher query for a Neo4j graph database.

Instructions:
- Use only the provided schema.
- Use only read clauses such as MATCH, OPTIONAL MATCH, WITH, WHERE, RETURN,
  ORDER BY, LIMIT, SKIP, and UNWIND.
- Never generate CREATE, MERGE, SET, DELETE, DETACH DELETE, REMOVE, LOAD CSV,
  CALL, APOC, dbms procedures, index changes, or schema changes.
- Return only the Cypher query.

Schema:
{schema}

Question:
{question}

Cypher query:
"""


@dataclass
class GraphNodeHit:
    labels: list[str]
    uuid: str
    props: dict[str, Any]
    matched_keys: list[str] = field(default_factory=list)


@dataclass
class GraphRelationshipHit:
    relation: str
    start_labels: list[str]
    start_uuid: str
    end_labels: list[str]
    end_uuid: str
    props: dict[str, Any]


@dataclass
class GraphRagResult:
    nodes: list[GraphNodeHit] = field(default_factory=list)
    relationships: list[GraphRelationshipHit] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def has_context(self):
        return bool(self.nodes or self.relationships)


def tokenize_query(text: str, max_terms=8) -> list[str]:
    words = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_-]{2,}", text.lower())
    terms = []
    for word in words:
        if word in STOPWORDS or word in terms:
            continue
        terms.append(word)
        if len(terms) >= max_terms:
            break
    return terms


def _safe_identifier(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or ""))


def _compact_props(props: dict[str, Any], limit=6) -> dict[str, Any]:
    result = {}
    for key, value in (props or {}).items():
        if key in META_PROPS or key == "uuid":
            continue
        if len(result) >= limit:
            break
        text = str(value)
        result[key] = text[:240] + ("..." if len(text) > 240 else "")
    return result


def available_graph_labels():
    try:
        from toto.sql_neo4j_sync.loader import load_all_configs
    except Exception:
        return []

    labels = []
    for config in load_all_configs():
        for node in config.get("nodes", []):
            label = node.get("label", "")
            if _safe_identifier(label) and label not in labels:
                labels.append(label)
    return labels


def langchain_neo4j_available() -> bool:
    return find_spec("langchain_neo4j") is not None


def build_langchain_neo4j_graph():
    """Build a LangChain Neo4jGraph, honoring ravioli's local fallback URIs."""
    if not langchain_neo4j_available():
        raise ImportError(
            "langchain-neo4j is not installed. Install it to enable Steven's "
            "GraphCypherQAChain tool."
        )

    from langchain_neo4j import Neo4jGraph
    from toto.ravioli.connection import connection_uris, is_connection_error

    errors = []
    for uri in connection_uris(settings.NEO4J_URI):
        try:
            return Neo4jGraph(
                url=uri,
                username=settings.NEO4J_USER,
                password=settings.NEO4J_PASSWORD,
            )
        except Exception as exc:
            if not is_connection_error(exc):
                raise
            errors.append(f"{uri}: {exc}")

    raise RuntimeError(
        "Could not create LangChain Neo4j graph. Tried "
        + ", ".join(connection_uris(settings.NEO4J_URI))
        + ". Last error: "
        + (errors[-1] if errors else "unknown")
    )


def build_graph_cypher_qa_chain(llm):
    """Build Neo4j's LangChain natural-language-to-Cypher QA chain."""
    if not langchain_neo4j_available():
        raise ImportError(
            "langchain-neo4j is not installed. Install it to enable Steven's "
            "GraphCypherQAChain tool."
        )

    from langchain_neo4j import GraphCypherQAChain

    try:
        from langchain_core.prompts import PromptTemplate
    except Exception:
        cypher_prompt = None
    else:
        cypher_prompt = PromptTemplate(
            input_variables=["schema", "question"],
            template=READ_ONLY_CYPHER_TEMPLATE,
        )

    kwargs = {
        "llm": llm,
        "graph": build_langchain_neo4j_graph(),
        "verbose": False,
        "allow_dangerous_requests": True,
    }
    if cypher_prompt is not None:
        kwargs["cypher_prompt"] = cypher_prompt
    while True:
        try:
            return GraphCypherQAChain.from_llm(**kwargs)
        except TypeError as exc:
            message = str(exc)
            removed = False
            if "cypher_prompt" in message and "cypher_prompt" in kwargs:
                kwargs.pop("cypher_prompt")
                removed = True
            if (
                "allow_dangerous_requests" in message
                and "allow_dangerous_requests" in kwargs
            ):
                kwargs.pop("allow_dangerous_requests")
                removed = True
            if not removed:
                raise


class GraphRagRetriever:
    def __init__(self, client=None):
        self.client = client

    def retrieve_for_agent(self, agent_profile, prompt: str) -> GraphRagResult:
        if not getattr(agent_profile, "graph_rag_enabled", False):
            return GraphRagResult()

        terms = tokenize_query(
            prompt,
            max_terms=getattr(settings, "STEVEN_GRAPH_RAG_MAX_TERMS", 8),
        )
        if not terms:
            return GraphRagResult(terms=terms)

        labels = self._labels_for_agent(agent_profile)
        max_nodes = max(1, int(getattr(agent_profile, "graph_rag_max_nodes", 8) or 8))
        depth = max(0, min(3, int(getattr(agent_profile, "graph_rag_depth", 1) or 1)))

        try:
            client = self._client()
            nodes = self._search_nodes(client, labels, terms, max_nodes)
            relationships = (
                self._relationships_for_nodes(client, nodes, depth)
                if depth and nodes
                else []
            )
            return GraphRagResult(nodes=nodes, relationships=relationships, terms=terms)
        except Exception as exc:
            return GraphRagResult(terms=terms, error=str(exc))

    def close(self):
        if self.client is not None:
            self.client.close()

    def _client(self):
        if self.client is not None:
            return self.client
        from toto.ravioli.connection import Neo4jClient

        self.client = Neo4jClient()
        return self.client

    def _labels_for_agent(self, agent_profile):
        configured = [
            label
            for label in (getattr(agent_profile, "graph_rag_labels", None) or [])
            if _safe_identifier(label)
        ]
        if configured:
            return configured
        return available_graph_labels()

    def _search_nodes(self, client, labels, terms, max_nodes):
        per_label_limit = max(1, max_nodes)
        hits = []
        seen = set()

        for label in labels:
            if len(hits) >= max_nodes:
                break
            records = client.run_cypher(
                (
                    f"MATCH (n:{label}) "
                    "WHERE n.uuid IS NOT NULL "
                    "WITH n, properties(n) AS props "
                    "WITH n, props, "
                    "[k IN keys(props) WHERE NOT k IN $meta_keys "
                    "AND any(term IN $terms WHERE "
                    "toLower(toString(props[k])) CONTAINS term)] AS matched_keys "
                    "WHERE size(matched_keys) > 0 "
                    "RETURN labels(n) AS labels, n.uuid AS uuid, "
                    "props, matched_keys "
                    "LIMIT $limit"
                ),
                {
                    "terms": terms,
                    "meta_keys": list(META_PROPS),
                    "limit": per_label_limit,
                },
            )
            for record in records:
                uuid = str(record["uuid"])
                key = (label, uuid)
                if key in seen:
                    continue
                seen.add(key)
                hits.append(GraphNodeHit(
                    labels=list(record["labels"]),
                    uuid=uuid,
                    props=_compact_props(record["props"]),
                    matched_keys=list(record["matched_keys"]),
                ))
                if len(hits) >= max_nodes:
                    break

        return hits

    def _relationships_for_nodes(self, client, nodes, depth):
        uuids = [node.uuid for node in nodes]
        limit = max(1, len(uuids) * 4)
        records = client.run_cypher(
            (
                f"MATCH path = (n)-[*1..{depth}]-(m) "
                "WHERE n.uuid IN $uuids AND m.uuid IS NOT NULL "
                "WITH relationships(path) AS rels "
                "UNWIND rels AS r "
                "WITH DISTINCT r "
                "RETURN type(r) AS relation, "
                "labels(startNode(r)) AS start_labels, "
                "startNode(r).uuid AS start_uuid, "
                "labels(endNode(r)) AS end_labels, "
                "endNode(r).uuid AS end_uuid, "
                "properties(r) AS props "
                "LIMIT $limit"
            ),
            {"uuids": uuids, "limit": limit},
        )
        result = []
        for record in records:
            if record["start_uuid"] is None or record["end_uuid"] is None:
                continue
            result.append(GraphRelationshipHit(
                relation=record["relation"],
                start_labels=list(record["start_labels"]),
                start_uuid=str(record["start_uuid"]),
                end_labels=list(record["end_labels"]),
                end_uuid=str(record["end_uuid"]),
                props=_compact_props(record["props"], limit=4),
            ))
        return result


def format_graph_context(result: GraphRagResult) -> str:
    if not result.has_context:
        return ""

    lines = [
        "Graph RAG context from Toto's Neo4j projection.",
        "Use this context only when relevant. Prefer explicit labels/uuids when citing graph facts.",
    ]

    if result.terms:
        lines.append("Matched terms: " + ", ".join(result.terms))

    if result.nodes:
        lines.append("")
        lines.append("Matched nodes:")
        for index, node in enumerate(result.nodes, start=1):
            label = ":".join(node.labels)
            props = "; ".join(f"{k}={v}" for k, v in node.props.items())
            matched = ", ".join(node.matched_keys)
            line = f"{index}. ({label} uuid={node.uuid})"
            if matched:
                line += f" matched={matched}"
            if props:
                line += f" props: {props}"
            lines.append(line)

    if result.relationships:
        lines.append("")
        lines.append("Nearby relationships:")
        for rel in result.relationships:
            start = ":".join(rel.start_labels)
            end = ":".join(rel.end_labels)
            props = "; ".join(f"{k}={v}" for k, v in rel.props.items())
            line = (
                f"- ({start} uuid={rel.start_uuid})"
                f"-[:{rel.relation}]-"
                f"({end} uuid={rel.end_uuid})"
            )
            if props:
                line += f" props: {props}"
            lines.append(line)

    return "\n".join(lines)
