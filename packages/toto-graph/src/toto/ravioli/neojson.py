"""
NeoJSON — serialized Neo4j graph data.

As GeoJSON is to geographic data, NeoJSON is to graph data: a JSON document that
represents Neo4j *nodes and relationships* — the data, not the query that produced
it.

A NeoJSON document is a single JSON object — a **Graph** — carrying separate
``nodes`` and ``relationships`` arrays.  Every member object is self-describing via
a ``type`` discriminator, mirroring GeoJSON's design philosophy.

Document shape::

    {
      "neojson": "1.0",
      "type": "Graph",
      "directed": true,
      "nodes": [
        {"type": "Node", "id": "n1", "labels": ["Person"],
         "properties": {"name": "Alice"}}
      ],
      "relationships": [
        {"type": "Relationship", "id": "r1", "relType": "KNOWS",
         "start": "n1", "end": "n2", "properties": {"since": 2020}}
      ],
      "metadata": {"node_count": 1, "relationship_count": 1,
                   "labels": ["Person"], "relationship_types": ["KNOWS"]}
    }

The full standard is documented in ``neojson_design.md`` at the repository root.

Field mapping (Neo4j -> NeoJSON)
--------------------------------
* Node                       -> Node object (``id``, ``labels``, ``properties``)
* Relationship               -> Relationship object
* Relationship type          -> ``relType``  (``type`` is the structural discriminator,
                                just as a GeoJSON Feature has both ``type:"Feature"``
                                and ``geometry.type``)
* start / end node           -> ``start`` / ``end`` (node ids)
* ``id(n)`` / ``elementId``  -> ``id`` (opaque string)

This module is the reference implementation.  ``dumps`` / ``loads`` round-trip
losslessly; ``from_ravioli`` bridges the dicts produced by
:meth:`toto.ravioli.connection.Neo4jClient.extract_graph`; ``to_networkx`` /
``from_networkx`` bridge the existing graph-analysis pipeline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

FORMAT_VERSION = "1.0"
MIME = "application/neojson+json"
EXTENSION = ".neojson"

TYPE_GRAPH = "Graph"
TYPE_NODE = "Node"
TYPE_RELATIONSHIP = "Relationship"


class NeoJsonParseError(ValueError):
    """Raised when a NeoJSON document cannot be parsed."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class NeoNode:
    id: str
    labels: list = field(default_factory=list)
    properties: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": TYPE_NODE,
            "id": self.id,
            "labels": list(self.labels),
            "properties": dict(self.properties),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NeoNode":
        if not isinstance(data, dict):
            raise NeoJsonParseError(f"Node must be an object, got {type(data).__name__}.")
        node_id = data.get("id")
        if node_id is None:
            raise NeoJsonParseError("Node is missing required 'id'.")
        labels = data.get("labels") or []
        if not isinstance(labels, list):
            raise NeoJsonParseError(f"Node {node_id!r} 'labels' must be an array.")
        props = data.get("properties") or {}
        if not isinstance(props, dict):
            raise NeoJsonParseError(f"Node {node_id!r} 'properties' must be an object.")
        return cls(id=str(node_id), labels=[str(x) for x in labels], properties=dict(props))


@dataclass
class NeoRelationship:
    rel_type: str
    start: str
    end: str
    id: "str | None" = None
    properties: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        out: dict = {"type": TYPE_RELATIONSHIP}
        if self.id is not None:
            out["id"] = self.id
        out["relType"] = self.rel_type
        out["start"] = self.start
        out["end"] = self.end
        out["properties"] = dict(self.properties)
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "NeoRelationship":
        if not isinstance(data, dict):
            raise NeoJsonParseError(
                f"Relationship must be an object, got {type(data).__name__}."
            )
        rel_type = data.get("relType")
        if not rel_type:
            raise NeoJsonParseError("Relationship is missing required 'relType'.")
        start = data.get("start")
        end = data.get("end")
        if start is None or end is None:
            raise NeoJsonParseError(
                f"Relationship {data.get('id')!r} requires 'start' and 'end'."
            )
        props = data.get("properties") or {}
        if not isinstance(props, dict):
            raise NeoJsonParseError(
                f"Relationship {data.get('id')!r} 'properties' must be an object."
            )
        rid = data.get("id")
        return cls(
            rel_type=str(rel_type),
            start=str(start),
            end=str(end),
            id=str(rid) if rid is not None else None,
            properties=dict(props),
        )


@dataclass
class NeoGraph:
    nodes: list = field(default_factory=list)          # list[NeoNode]
    relationships: list = field(default_factory=list)  # list[NeoRelationship]
    directed: bool = True
    metadata: dict = field(default_factory=dict)

    def _auto_metadata(self) -> dict:
        labels = sorted({lbl for n in self.nodes for lbl in n.labels})
        rel_types = sorted({r.rel_type for r in self.relationships if r.rel_type})
        return {
            "node_count": len(self.nodes),
            "relationship_count": len(self.relationships),
            "labels": labels,
            "relationship_types": rel_types,
        }

    def to_dict(self) -> dict:
        # Computed counts/inventories always reflect actual content; any other
        # explicit metadata (e.g. generated_at, source) is preserved.
        meta = {**self.metadata, **self._auto_metadata()}
        out = {
            "neojson": FORMAT_VERSION,
            "type": TYPE_GRAPH,
            "directed": bool(self.directed),
            "nodes": [n.to_dict() for n in self.nodes],
            "relationships": [r.to_dict() for r in self.relationships],
        }
        if meta:
            out["metadata"] = meta
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "NeoGraph":
        if not isinstance(data, dict):
            raise NeoJsonParseError(
                f"NeoJSON root must be an object, got {type(data).__name__}."
            )
        root_type = data.get("type")
        if root_type not in (TYPE_GRAPH, None):
            raise NeoJsonParseError(f"Expected root type 'Graph', got {root_type!r}.")
        nodes = [NeoNode.from_dict(n) for n in (data.get("nodes") or [])]
        rels = [NeoRelationship.from_dict(r) for r in (data.get("relationships") or [])]
        directed = data.get("directed")
        directed = True if directed is None else bool(directed)
        metadata = data.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        return cls(nodes=nodes, relationships=rels, directed=directed, metadata=dict(metadata))


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def dumps(graph: "NeoGraph", *, indent: int = 2) -> str:
    """Serialize a NeoGraph to a NeoJSON string (``default=str`` handles temporal
    / Decimal property values, mirroring the existing graph-analysis output)."""
    return json.dumps(graph.to_dict(), indent=indent, ensure_ascii=False, default=str) + "\n"


def loads(text: str) -> "NeoGraph":
    """Parse a NeoJSON string into a NeoGraph. Empty input yields an empty graph."""
    text = (text or "").strip()
    if not text:
        return new_graph()
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise NeoJsonParseError(f"Invalid NeoJSON document: {exc}") from exc
    return NeoGraph.from_dict(data)


def new_graph(*, directed: bool = True) -> "NeoGraph":
    """An empty graph — used as the initial content for new ``.neojson`` files."""
    return NeoGraph(nodes=[], relationships=[], directed=directed)


# ---------------------------------------------------------------------------
# Bridges
# ---------------------------------------------------------------------------

def from_ravioli(nodes, edges, *, metadata=None, directed: bool = True) -> "NeoGraph":
    """
    Build a NeoGraph from ravioli's ``Neo4jClient.extract_graph`` output.

    ``nodes``: list of ``{"id", "labels", "props"}``
    ``edges``: list of ``{"id", "type", "start", "end", "props"}``
    """
    neo_nodes = [
        NeoNode(
            id=str(n.get("id")),
            labels=list(n.get("labels") or []),
            properties=dict(n.get("props") or {}),
        )
        for n in (nodes or [])
    ]
    neo_rels = [
        NeoRelationship(
            rel_type=str(e.get("type") or ""),
            start=str(e.get("start")),
            end=str(e.get("end")),
            id=str(e["id"]) if e.get("id") is not None else None,
            properties=dict(e.get("props") or {}),
        )
        for e in (edges or [])
    ]
    return NeoGraph(
        nodes=neo_nodes,
        relationships=neo_rels,
        directed=directed,
        metadata=dict(metadata or {}),
    )


def to_networkx(graph: "NeoGraph"):
    """Build a networkx ``MultiDiGraph`` from a NeoGraph (same conventions as
    :func:`toto.ravioli.graph_analysis.build_networkx_graph`)."""
    try:
        import networkx as nx
    except ImportError as exc:  # pragma: no cover - exercised only without networkx
        raise ImportError("networkx is required for to_networkx().") from exc

    G = nx.MultiDiGraph()
    for n in graph.nodes:
        G.add_node(n.id, labels=list(n.labels), **n.properties)
    for r in graph.relationships:
        G.add_edge(r.start, r.end, key=r.id, edge_type=r.rel_type, **r.properties)
    return G


def from_networkx(G, *, directed: bool = True, metadata=None) -> "NeoGraph":
    """Build a NeoGraph from a networkx graph that follows the :func:`to_networkx`
    convention (``labels`` node attr, ``edge_type`` edge attr, edge key as rel id)."""
    nodes = []
    for node_id, attrs in G.nodes(data=True):
        attrs = dict(attrs)
        labels = attrs.pop("labels", None) or []
        nodes.append(NeoNode(id=str(node_id), labels=list(labels), properties=attrs))

    rels = []
    if G.is_multigraph():
        for start, end, key, attrs in G.edges(keys=True, data=True):
            attrs = dict(attrs)
            rel_type = attrs.pop("edge_type", "") or ""
            rels.append(NeoRelationship(
                rel_type=str(rel_type), start=str(start), end=str(end),
                id=str(key) if key is not None else None, properties=attrs,
            ))
    else:
        for start, end, attrs in G.edges(data=True):
            attrs = dict(attrs)
            rel_type = attrs.pop("edge_type", "") or ""
            rels.append(NeoRelationship(
                rel_type=str(rel_type), start=str(start), end=str(end), properties=attrs,
            ))

    return NeoGraph(nodes=nodes, relationships=rels, directed=directed,
                    metadata=dict(metadata or {}))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate(obj) -> list:
    """
    Return a list of human-readable problems with a NeoJSON document.

    Accepts a parsed ``dict``, a JSON ``str``, or a :class:`NeoGraph`.
    An empty list means the document is valid.
    """
    if isinstance(obj, NeoGraph):
        data = obj.to_dict()
    elif isinstance(obj, str):
        try:
            data = json.loads(obj)
        except (ValueError, TypeError) as exc:
            return [f"Not valid JSON: {exc}"]
    elif isinstance(obj, dict):
        data = obj
    else:
        return [f"NeoJSON must be an object, got {type(obj).__name__}."]

    if not isinstance(data, dict):
        return [f"NeoJSON root must be an object, got {type(data).__name__}."]

    errors: list = []
    if "neojson" not in data:
        errors.append("Missing 'neojson' version member.")
    if data.get("type") != TYPE_GRAPH:
        errors.append(f"Root 'type' must be 'Graph', got {data.get('type')!r}.")

    nodes = data.get("nodes")
    rels = data.get("relationships")
    if not isinstance(nodes, list):
        errors.append("'nodes' must be an array.")
        nodes = []
    if not isinstance(rels, list):
        errors.append("'relationships' must be an array.")
        rels = []

    node_ids = set()
    for i, n in enumerate(nodes):
        if not isinstance(n, dict):
            errors.append(f"nodes[{i}] must be an object.")
            continue
        if n.get("type") not in (TYPE_NODE, None):
            errors.append(f"nodes[{i}] 'type' must be 'Node'.")
        nid = n.get("id")
        if nid is None:
            errors.append(f"nodes[{i}] is missing 'id'.")
            continue
        if str(nid) in node_ids:
            errors.append(f"Duplicate node id {nid!r}.")
        node_ids.add(str(nid))

    for i, r in enumerate(rels):
        if not isinstance(r, dict):
            errors.append(f"relationships[{i}] must be an object.")
            continue
        if r.get("type") not in (TYPE_RELATIONSHIP, None):
            errors.append(f"relationships[{i}] 'type' must be 'Relationship'.")
        if not r.get("relType"):
            errors.append(f"relationships[{i}] is missing 'relType'.")
        start = r.get("start")
        end = r.get("end")
        if start is None or end is None:
            errors.append(f"relationships[{i}] requires 'start' and 'end'.")
            continue
        if str(start) not in node_ids:
            errors.append(f"relationships[{i}] 'start' {start!r} references unknown node.")
        if str(end) not in node_ids:
            errors.append(f"relationships[{i}] 'end' {end!r} references unknown node.")

    return errors
