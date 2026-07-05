"""Records + mapping_spec → the ingestor's proposal dict. Deterministic, no LLM.

The structured-data analogue of ``ingestor.services.strategies.llm._normalize``:
node/relationship elements come out in the exact proposal schema, so the whole
ingestor review/validation/apply machinery works unchanged downstream. The
caller (``runner``) is responsible for the final ``validation.revalidate`` +
``summarize`` pass, mirroring the strategy → pipeline division of labour.

Dedupe is two-level:

- within the run, by ``(category_slug, normalized identifier)`` — repeated
  records reuse one temp_id and just gain evidence entries;
- against the live graph, via the ingestor catalog + rapidfuzz matcher
  restricted to the rule's category — an exact match becomes an ``existing``
  reference (no new node), a near match gets a ``duplicate_warning``.

``bento.graph_service.create_edge`` is a Cypher CREATE (not MERGE), so
relationships between two *existing* nodes are additionally checked against the
graph and skipped when already present — a periodic connector re-run must not
multiply edges.
"""

import json

from toto.ingestor.services import matching
from toto.ingestor.services.text import normalize

from . import mapping

# Fixed deterministic confidence tiers. The ingestor's scoring formulas are
# keyed to NER/trigger signals that don't exist for structured records.
CONFIDENCE_EXISTING = 1.0
CONFIDENCE_NEW = 0.9
CONFIDENCE_NEW_DUPLICATE = 0.6
CONFIDENCE_RELATIONSHIP = 0.9

_EVIDENCE_SNIPPET_CHARS = 200


def _bento_schema():
    """(categories, edge_types, searchable) — same shape as the LLM strategy's."""
    from toto.ingestor.services.strategies.llm import _bento_schema as bento_schema

    return bento_schema()


def _edge_exists(edge_type_slug, from_uid, to_uid):
    """True when the graph already holds this exact edge (existing→existing only)."""
    from toto.bento import graph_service

    return graph_service.edge_exists(edge_type_slug, from_uid, to_uid)


def _resolve_properties(record, rule):
    """Resolve a rule's mapped properties on a record, dropping misses."""
    props = {}
    for prop, source in (rule.get("properties") or {}).items():
        value = mapping.resolve_value(record, source)
        if value is not None:
            props[prop] = value
    return props


def _evidence(record, index, rule_id):
    snippet = json.dumps(record, ensure_ascii=False, default=str)
    if len(snippet) > _EVIDENCE_SNIPPET_CHARS:
        snippet = snippet[: _EVIDENCE_SNIPPET_CHARS - 1] + "…"
    return {
        "text": f"record {index} · {snippet}",
        "record_index": index,
        "rule_id": rule_id,
    }


def transform(records, spec, *, entries, categories=None,
              edge_types=None, searchable=None):
    """Return ``(proposal, tstats)``.

    ``entries`` is the ingestor catalog (:class:`CatalogEntry` list) built once
    per run by the caller. ``categories``/``edge_types``/``searchable`` default
    to the live Bento templates and are injectable for tests.
    """
    if categories is None or edge_types is None or searchable is None:
        categories, edge_types, searchable = _bento_schema()

    node_rules = spec.get("nodes") or []
    rel_rules = spec.get("relationships") or []

    # Matching is restricted per category; only index the categories the spec
    # actually maps (the catalog may span many more).
    mapped_slugs = {rule.get("category_slug") for rule in node_rules}
    entries_by_cat = {}
    for entry in entries:
        if entry.category_slug in mapped_slugs:
            entries_by_cat.setdefault(entry.category_slug, []).append(entry)
    index_by_cat = {slug: matching.build_index(es) for slug, es in entries_by_cat.items()}

    nodes = []
    relationships = []
    nodes_by_key = {}       # (category_slug, normalized surface) -> node element
    node_by_temp = {}       # temp_id -> node element
    rels_seen = set()       # (edge_type_slug, from_temp, to_temp)
    skipped = []
    stats = {
        "records": len(records),
        "nodes_proposed": 0,
        "existing_matched": 0,
        "relationships_proposed": 0,
        "edges_already_in_graph": 0,
    }

    for record_index, record in enumerate(records):
        # node rules first: relationship rules need this record's temp_ids
        temp_by_rule = {}
        for rule in node_rules:
            rule_id = rule.get("rule_id")
            slug = rule.get("category_slug")
            if slug not in categories:
                skipped.append({"record": record_index, "rule": rule_id,
                                "reason": f"unknown category '{slug}'"})
                continue
            if not mapping.check_when(record, rule.get("when")):
                continue
            surface = mapping.resolve_value(record, rule.get("identifier") or {})
            surface = str(surface).strip() if surface is not None else ""
            if not surface:
                skipped.append({"record": record_index, "rule": rule_id,
                                "reason": "empty identifier"})
                continue

            key = (slug, normalize(surface))
            node = nodes_by_key.get(key)
            if node is not None:
                node["evidence"].append(_evidence(record, record_index, rule_id))
                temp_by_rule[rule_id] = node["temp_id"]
                continue

            cat_entries = entries_by_cat.get(slug, [])
            candidates, is_duplicate = matching.match_surface(
                surface, cat_entries, index_by_cat.get(slug)
            )
            temp_id = f"n{len(nodes) + 1}"
            if candidates and candidates[0]["method"] == "exact":
                uid = candidates[0]["uid"]
                node = {
                    "temp_id": temp_id, "kind": "existing", "category_slug": slug,
                    "category_name": categories[slug], "uid": uid,
                    "display": candidates[0]["display"] or surface, "properties": {},
                    "evidence": [_evidence(record, record_index, rule_id)],
                    "match": {"method": "exact", "matched_uid": uid,
                              "score": 1.0, "candidates": candidates},
                    "duplicate_warning": False, "merge_into_uid": None,
                    "ner_label": None, "confidence": CONFIDENCE_EXISTING,
                    "approval": "pending", "validation": {},
                }
                stats["existing_matched"] += 1
            else:
                props = _resolve_properties(record, rule)
                # Fill the category's identifier property so required fields
                # (e.g. 'title') aren't left empty — same rule as the LLM path.
                ident = searchable.get(slug) or "name"
                if not props.get(ident):
                    props[ident] = surface
                node = {
                    "temp_id": temp_id, "kind": "new", "category_slug": slug,
                    "category_name": categories[slug], "uid": None,
                    "display": surface, "properties": props,
                    "evidence": [_evidence(record, record_index, rule_id)],
                    "match": {
                        "method": "fuzzy", "matched_uid": None,
                        "score": candidates[0]["score"], "candidates": candidates,
                    } if candidates else None,
                    "duplicate_warning": bool(is_duplicate),
                    "merge_into_uid": None, "ner_label": None,
                    "confidence": CONFIDENCE_NEW_DUPLICATE if is_duplicate else CONFIDENCE_NEW,
                    "approval": "pending", "validation": {},
                }
                stats["nodes_proposed"] += 1
            nodes.append(node)
            nodes_by_key[key] = node
            node_by_temp[temp_id] = node
            temp_by_rule[rule_id] = temp_id

        for rule in rel_rules:
            rule_id = rule.get("rule_id")
            slug = rule.get("edge_type_slug")
            if slug not in edge_types:
                skipped.append({"record": record_index, "rule": rule_id,
                                "reason": f"unknown edge type '{slug}'"})
                continue
            if not mapping.check_when(record, rule.get("when")):
                continue
            from_temp = temp_by_rule.get(rule.get("from_rule"))
            to_temp = temp_by_rule.get(rule.get("to_rule"))
            if not from_temp or not to_temp:
                skipped.append({"record": record_index, "rule": rule_id,
                                "reason": "endpoint rule did not fire"})
                continue
            rel_key = (slug, from_temp, to_temp)
            if rel_key in rels_seen:
                continue
            rels_seen.add(rel_key)

            from_node = node_by_temp.get(from_temp)
            to_node = node_by_temp.get(to_temp)
            if (
                from_node is not None and to_node is not None
                and from_node["kind"] == "existing" and to_node["kind"] == "existing"
                and _edge_exists(slug, from_node["uid"], to_node["uid"])
            ):
                stats["edges_already_in_graph"] += 1
                skipped.append({"record": record_index, "rule": rule_id,
                                "reason": "edge already in graph"})
                continue

            props = _resolve_properties(record, rule)
            relationships.append({
                "temp_id": f"r{len(relationships) + 1}",
                "edge_type_slug": slug,
                "rel_type": edge_types[slug]["rel_type"],
                "edge_type_name": edge_types[slug]["name"],
                "from": from_temp, "to": to_temp, "properties": props,
                "evidence": [_evidence(record, record_index, rule_id)],
                "trigger_matched": False, "confidence": CONFIDENCE_RELATIONSHIP,
                "approval": "pending", "validation": {},
            })
            stats["relationships_proposed"] += 1

    proposal = {"nodes": nodes, "relationships": relationships}
    tstats = {**stats, "skipped": skipped}
    return proposal, tstats
