"""The one and only place in ``bento`` that talks to Neo4j.

Bento is a first-class Neo4j editor. All graph reads/writes funnel through this
module so the rest of the app never imports neo4j/neomodel directly. We use:

* **neomodel dynamic classes** (from :mod:`toto.bento.registry`) for typed node
  create/update — they validate and coerce properties against the template.
* **ravioli's** :class:`~toto.ravioli.connection.Neo4jClient` for raw reads,
  listing/search/pagination, edges, batch delete and lazy graph extraction.

Both connect to the same database; ravioli owns the neomodel connection setup
(:func:`toto.ravioli.neomodel_conn.ensure_configured`). Every public function
raises :class:`GraphUnavailable` when ``RAVIOLI_ENABLED`` is False so views can
render a graceful "graph unavailable" state instead of crashing.
"""

import json
from contextlib import contextmanager

from django.utils.dateparse import parse_datetime

from .models import BentoCategory, BentoEdgeType
from . import registry


class GraphUnavailable(RuntimeError):
    """Neo4j is disabled — the feature cannot run right now."""


class GraphValidationError(ValueError):
    """A node/edge violated its template (bad type, missing required, etc.)."""


class NotFound(LookupError):
    """A node or edge could not be found."""


# --------------------------------------------------------------------------
# connection guards
# --------------------------------------------------------------------------

def _require_graph():
    from toto.ravioli.connection import is_enabled

    if not is_enabled():
        raise GraphUnavailable("Neo4j is not available (RAVIOLI_ENABLED is False).")


def _require_neomodel():
    _require_graph()
    from toto.ravioli.neomodel_conn import ensure_configured

    ensure_configured()


@contextmanager
def _client():
    from toto.ravioli.connection import Neo4jClient

    c = Neo4jClient()
    try:
        yield c
    finally:
        c.close()


# --------------------------------------------------------------------------
# template helpers
# --------------------------------------------------------------------------

def _labels_map():
    """Return {neo4j_label: BentoCategory} for all node templates."""
    return {c.neo4j_label: c for c in BentoCategory.objects.all() if c.neo4j_label}


def _bento_labels():
    return list(_labels_map().keys())


def _schema_index(schema):
    return {f["name"]: f for f in (schema or []) if isinstance(f, dict) and f.get("name")}


def _coerce(ftype, value):
    if value is None or value == "":
        return None
    try:
        if ftype in ("string", "text"):
            return str(value)
        if ftype == "integer":
            return int(value)
        if ftype == "float":
            return float(value)
        if ftype == "boolean":
            return value in (True, "true", "True", "on", "1", 1)
        if ftype == "datetime":
            return value if not isinstance(value, str) else (parse_datetime(value) or value)
        if ftype == "json":
            return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError) as exc:
        raise GraphValidationError(f"Invalid {ftype} value: {value!r} ({exc})")
    return value


def _split_props(schema, props):
    """Split incoming props into (declared, extra), coercing declared by type.

    Unknown keys land in ``extra``. Missing required declared fields raise.
    """
    index = _schema_index(schema)
    declared, extra = {}, {}
    for key, value in (props or {}).items():
        if key in index:
            coerced = _coerce(index[key].get("type"), value)
            if coerced is not None:
                declared[key] = coerced
        else:
            extra[key] = value
    for name, field in index.items():
        if field.get("required") and declared.get(name) in (None, ""):
            raise GraphValidationError(f"'{name}' is required.")
    return declared, extra


def _to_cypher_value(ftype, value):
    """Coerce a declared value into a Neo4j-storable primitive (for edges)."""
    if ftype == "json":
        return json.dumps(value)
    if ftype == "datetime" and value is not None and not isinstance(value, str):
        return value.isoformat()
    return value


def _display_name(props):
    for key in ("name", "title", "label"):
        if props.get(key):
            return str(props[key])
    uid = props.get("uid") or ""
    return uid[:8] or "node"


def _serialize_node(props, labels):
    props = dict(props)
    extra = props.get("extra")
    if isinstance(extra, str):
        try:
            props["extra"] = json.loads(extra)
        except ValueError:
            props["extra"] = {}
    label = next((lbl for lbl in labels if lbl in _labels_map()), labels[0] if labels else "")
    category = _labels_map().get(label)
    return {
        "uid": props.get("uid"),
        "label": label,
        "category_slug": category.slug if category else None,
        "category_name": category.name if category else label,
        "display": _display_name(props),
        "properties": props,
    }


# --------------------------------------------------------------------------
# node CRUD
# --------------------------------------------------------------------------

def create_node(cat_slug, props):
    _require_neomodel()
    cat = BentoCategory.objects.filter(slug=cat_slug).first()
    if not cat:
        raise NotFound(f"Unknown category '{cat_slug}'.")
    declared, extra = _split_props(cat.property_schema, props)
    klass = registry.node_class_for(cat)
    node = klass(extra=extra, **declared)
    node.save()
    stored = {**declared, "extra": extra, "uid": node.uid}
    return _serialize_node(stored, [cat.neo4j_label])


def get_node(uid):
    _require_graph()
    with _client() as c:
        records = c.run_cypher(
            "MATCH (n {uid: $uid}) RETURN n AS n, labels(n) AS labels LIMIT 1",
            {"uid": uid},
        )
    if not records:
        raise NotFound(f"Node '{uid}' not found.")
    rec = records[0]
    return _serialize_node(dict(rec["n"]), list(rec["labels"]))


def list_nodes(cat_slug=None, q=None, limit=25, offset=0):
    """Return ``(rows, total)``. ``cat_slug`` filters by type; ``q`` quick-searches."""
    _require_graph()
    labels = [BentoCategory.objects.get(slug=cat_slug).neo4j_label] if cat_slug else _bento_labels()
    if not labels:
        return [], 0
    q = (q or "").strip()
    where = (
        "any(l IN labels(n) WHERE l IN $labels) "
        "AND ($q = '' OR any(k IN keys(n) WHERE toLower(toString(n[k])) CONTAINS toLower($q)))"
    )
    params = {"labels": labels, "q": q, "limit": int(limit), "offset": int(offset)}
    with _client() as c:
        total = c.run_cypher(f"MATCH (n) WHERE {where} RETURN count(n) AS total", params)[0]["total"]
        records = c.run_cypher(
            f"MATCH (n) WHERE {where} RETURN n AS n, labels(n) AS labels "
            "ORDER BY n.uid SKIP $offset LIMIT $limit",
            params,
        )
    rows = [_serialize_node(dict(r["n"]), list(r["labels"])) for r in records]
    return rows, total


def update_node(uid, props):
    _require_neomodel()
    current = get_node(uid)
    cat = BentoCategory.objects.filter(slug=current["category_slug"]).first()
    if not cat:
        raise NotFound(f"Node '{uid}' has no known category.")
    declared, extra = _split_props(cat.property_schema, props)
    klass = registry.node_class_for(cat)
    node = klass.nodes.get_or_none(uid=uid)
    if node is None:
        raise NotFound(f"Node '{uid}' not found.")
    for key, value in declared.items():
        setattr(node, key, value)
    if extra:
        merged = dict(node.extra or {})
        merged.update(extra)
        node.extra = merged
    node.save()
    return get_node(uid)


def delete_node(uid):
    delete_nodes([uid])


def delete_nodes(uids):
    """Batch delete nodes (and their relationships) by uid."""
    _require_graph()
    uids = [u for u in (uids or []) if u]
    if not uids:
        return 0
    with _client() as c:
        c.run_cypher("MATCH (n) WHERE n.uid IN $uids DETACH DELETE n", {"uids": uids})
    return len(uids)


# --------------------------------------------------------------------------
# edge CRUD
# --------------------------------------------------------------------------

def _edge_payload(edge_type, props):
    declared, extra = _split_props(edge_type.property_schema, props)
    index = _schema_index(edge_type.property_schema)
    payload = {name: _to_cypher_value(index[name].get("type"), val) for name, val in declared.items()}
    payload["extra"] = json.dumps(extra)
    return payload


def _serialize_edge(rec):
    props = dict(rec["props"])
    extra = props.get("extra")
    if isinstance(extra, str):
        try:
            props["extra"] = json.loads(extra)
        except ValueError:
            props["extra"] = {}
    et = BentoEdgeType.objects.filter(rel_type=rec["type"]).first()
    # Endpoint display names when the query returned them (aprops/bprops).
    keys = rec.keys() if hasattr(rec, "keys") else rec
    source_display = _display_name(dict(rec["aprops"])) if "aprops" in keys else rec["start"]
    target_display = _display_name(dict(rec["bprops"])) if "bprops" in keys else rec["end"]
    return {
        "id": rec["id"],
        "type": rec["type"],
        "edge_type_slug": et.slug if et else None,
        "edge_type_name": et.name if et else rec["type"],
        "source": rec["start"],
        "target": rec["end"],
        "source_display": source_display,
        "target_display": target_display,
        "properties": props,
    }


def create_edge(et_slug, from_uid, to_uid, props=None):
    _require_graph()
    et = BentoEdgeType.objects.filter(slug=et_slug).first()
    if not et:
        raise NotFound(f"Unknown edge type '{et_slug}'.")
    source, target = get_node(from_uid), get_node(to_uid)
    _check_allowed(et, source, target)
    payload = _edge_payload(et, props or {})
    with _client() as c:
        records = c.run_cypher(
            f"MATCH (a {{uid: $from}}), (b {{uid: $to}}) "
            f"CREATE (a)-[r:{et.rel_type} $props]->(b) "
            "RETURN elementId(r) AS id, type(r) AS type, a.uid AS start, "
            "b.uid AS end, properties(r) AS props",
            {"from": from_uid, "to": to_uid, "props": payload},
        )
    if not records:
        raise NotFound("Source or target node not found.")
    return _serialize_edge(records[0])


def _check_allowed(edge_type, source, target):
    allowed_src = list(edge_type.allowed_sources.values_list("slug", flat=True))
    allowed_tgt = list(edge_type.allowed_targets.values_list("slug", flat=True))
    if allowed_src and source["category_slug"] not in allowed_src:
        raise GraphValidationError(
            f"'{source['category_name']}' is not an allowed source for '{edge_type.name}'."
        )
    if allowed_tgt and target["category_slug"] not in allowed_tgt:
        raise GraphValidationError(
            f"'{target['category_name']}' is not an allowed target for '{edge_type.name}'."
        )


def list_edges(node_uid=None, et_slug=None, q=None, limit=25, offset=0):
    """Return ``(rows, total)``. Filter by incident node, edge type, and search."""
    _require_graph()
    labels = _bento_labels()
    if not labels:
        return [], 0
    rel = BentoEdgeType.objects.get(slug=et_slug).rel_type if et_slug else ""
    q = (q or "").strip()
    where = (
        "any(l IN labels(a) WHERE l IN $labels) AND any(l IN labels(b) WHERE l IN $labels) "
        "AND ($node = '' OR a.uid = $node OR b.uid = $node) "
        "AND ($rel = '' OR type(r) = $rel) "
        "AND ($q = '' OR any(k IN keys(r) WHERE toLower(toString(r[k])) CONTAINS toLower($q)))"
    )
    params = {
        "labels": labels, "node": node_uid or "", "rel": rel, "q": q,
        "limit": int(limit), "offset": int(offset),
    }
    with _client() as c:
        total = c.run_cypher(
            f"MATCH (a)-[r]->(b) WHERE {where} RETURN count(r) AS total", params
        )[0]["total"]
        records = c.run_cypher(
            f"MATCH (a)-[r]->(b) WHERE {where} "
            "RETURN elementId(r) AS id, type(r) AS type, a.uid AS start, "
            "b.uid AS end, properties(r) AS props, "
            "properties(a) AS aprops, properties(b) AS bprops "
            "ORDER BY id SKIP $offset LIMIT $limit",
            params,
        )
    return [_serialize_edge(r) for r in records], total


def get_edge(edge_id):
    _require_graph()
    with _client() as c:
        records = c.run_cypher(
            "MATCH (a)-[r]->(b) WHERE elementId(r) = $id "
            "RETURN elementId(r) AS id, type(r) AS type, a.uid AS start, "
            "b.uid AS end, properties(r) AS props, "
            "properties(a) AS aprops, properties(b) AS bprops",
            {"id": edge_id},
        )
    if not records:
        raise NotFound(f"Edge '{edge_id}' not found.")
    return _serialize_edge(records[0])


def update_edge(edge_id, props):
    _require_graph()
    with _client() as c:
        type_recs = c.run_cypher(
            "MATCH ()-[r]->() WHERE elementId(r) = $id RETURN type(r) AS type",
            {"id": edge_id},
        )
        if not type_recs:
            raise NotFound(f"Edge '{edge_id}' not found.")
        et = BentoEdgeType.objects.filter(rel_type=type_recs[0]["type"]).first()
        if not et:
            raise NotFound("Edge has no known edge type.")
        payload = _edge_payload(et, props)
        records = c.run_cypher(
            "MATCH (a)-[r]->(b) WHERE elementId(r) = $id SET r += $props "
            "RETURN elementId(r) AS id, type(r) AS type, a.uid AS start, "
            "b.uid AS end, properties(r) AS props",
            {"id": edge_id, "props": payload},
        )
    return _serialize_edge(records[0])


def delete_edge(edge_id):
    delete_edges([edge_id])


def delete_edges(edge_ids):
    """Batch delete relationships by elementId."""
    _require_graph()
    ids = [e for e in (edge_ids or []) if e]
    if not ids:
        return 0
    with _client() as c:
        c.run_cypher("MATCH ()-[r]->() WHERE elementId(r) IN $ids DELETE r", {"ids": ids})
    return len(ids)


# --------------------------------------------------------------------------
# graph (lazy) for Cytoscape
# --------------------------------------------------------------------------

def _graph_payload(node_records, edge_records):
    nodes = [
        {
            "id": n["uid"], "label": n["display"], "category": n["category_slug"],
            "properties": n["properties"],
        }
        for n in node_records
    ]
    edges = [
        {
            "id": e["id"], "source": e["source"], "target": e["target"],
            "label": e["edge_type_name"], "type": e["type"],
        }
        for e in edge_records
    ]
    return {"nodes": nodes, "edges": edges}


def node_graph(uid, depth=1):
    """Lazy neighborhood around a node, ``depth`` hops out, for tap-to-expand."""
    _require_graph()
    labels = _bento_labels()
    depth = max(1, min(int(depth), 4))
    where_m = "m IS NULL OR any(l IN labels(m) WHERE l IN $labels)"
    with _client() as c:
        records = c.run_cypher(
            f"MATCH (root {{uid: $uid}}) "
            f"OPTIONAL MATCH (root)-[r*1..{depth}]-(m) WHERE {where_m} "
            "RETURN root, r, m",
            {"uid": uid, "labels": labels},
        )
    return _shape_records(records, labels)


def full_graph(cat_slug=None, et_slug=None, q=None, limit=200):
    """Bounded whole-graph view honoring the same filters as the lists."""
    _require_graph()
    node_rows, _ = list_nodes(cat_slug=cat_slug, q=q, limit=limit, offset=0)
    uids = [n["uid"] for n in node_rows]
    if not uids:
        return {"nodes": [], "edges": []}
    rel = BentoEdgeType.objects.get(slug=et_slug).rel_type if et_slug else ""
    with _client() as c:
        edge_recs = c.run_cypher(
            "MATCH (a)-[r]->(b) WHERE a.uid IN $uids AND b.uid IN $uids "
            "AND ($rel = '' OR type(r) = $rel) "
            "RETURN elementId(r) AS id, type(r) AS type, a.uid AS start, "
            "b.uid AS end, properties(r) AS props",
            {"uids": uids, "rel": rel},
        )
    edges = [_serialize_edge(r) for r in edge_recs]
    return _graph_payload(node_rows, edges)


def _shape_records(records, labels):
    """Turn root/r/m cypher rows into a uid-keyed Cytoscape payload."""
    nodes, edges, seen_n, seen_e = [], [], set(), set()

    def add_node(neo_node):
        data = dict(neo_node)
        uid = data.get("uid")
        if not uid or uid in seen_n:
            return
        seen_n.add(uid)
        nodes.append(_serialize_node(data, list(neo_node.labels)))

    for rec in records:
        if rec.get("root") is not None:
            add_node(rec["root"])
        if rec.get("m") is not None:
            add_node(rec["m"])
        rels = rec.get("r") or []
        if not isinstance(rels, list):
            rels = [rels]
        for rel in rels:
            if rel is None:
                continue
            eid = rel.element_id
            if eid in seen_e:
                continue
            seen_e.add(eid)
            start = dict(rel.start_node).get("uid")
            end = dict(rel.end_node).get("uid")
            if start and end:
                edges.append(_serialize_edge({
                    "id": eid, "type": rel.type, "start": start, "end": end,
                    "props": dict(rel),
                }))
    return _graph_payload(nodes, edges)
