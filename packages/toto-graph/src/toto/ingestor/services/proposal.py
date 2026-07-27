"""Assemble a staged graph patch (the proposal JSON) from detection results.

Output shape (stored in ``IngestProposal.proposal``)::

    {
      "nodes": [ {temp_id, kind, category_slug, category_name, uid, display,
                  properties, evidence[], confidence, match, duplicate_warning,
                  validation, approval, merge_into_uid, ner_label} ],
      "relationships": [ {temp_id, edge_type_slug, rel_type, edge_type_name,
                  from, to, properties, evidence[], confidence, trigger_matched,
                  validation, approval} ]
    }

Nodes/relationships reference each other by ``temp_id``; the patch is *not* graph
data — applying it writes through ``bento.graph_service``.
"""

from toto.bento import graph_service
from toto.bento.models import BentoCategory

from . import matching, relations, scoring
from .validation import revalidate


def _node_key(mention, category_slug):
    if mention.kind == "existing":
        return ("existing", mention.uid)
    return ("new", category_slug or "", mention.normalized)


def assemble(detection, *, include_existing_nodes=False):
    from .catalog import build_catalog  # avoid import cycle at module load

    catalog_entries, catalog_meta = build_catalog()
    return (
        assemble_from_catalog(detection, catalog_entries, include_existing_nodes=include_existing_nodes),
        catalog_meta,
    )


def assemble_from_catalog(detection, catalog_entries, *, include_existing_nodes=False):
    """Build the proposal dict from a detection result + a prebuilt catalog.

    ``include_existing_nodes`` lets relationship-building pair entities with
    existing graph nodes named anywhere in the text (not just same-sentence).
    """
    cat_objs = {c.slug: c for c in BentoCategory.objects.all()}
    cat_name = {slug: c.name for slug, c in cat_objs.items()}
    display_by_uid = {}
    for e in catalog_entries:
        display_by_uid.setdefault(e.uid, e.display)
    index = matching.build_index(catalog_entries)

    mentions = sorted(detection.mentions, key=lambda m: (m.start_char, m.end_char))

    nodes_by_key, key_order, mention_key = {}, [], {}

    for m in mentions:
        category_slug = m.category_slug
        key = _node_key(m, category_slug)
        mention_key[id(m)] = key
        ev = {"text": m.text, "start": m.start_char, "end": m.end_char, "sentence": m.sent_index}

        if key in nodes_by_key:
            nodes_by_key[key]["evidence"].append(ev)
            continue
        key_order.append(key)

        if m.kind == "existing":
            node = {
                "kind": "existing",
                "category_slug": category_slug,
                "category_name": cat_name.get(category_slug, category_slug),
                "uid": m.uid,
                "display": display_by_uid.get(m.uid, m.text),
                "properties": {},
                "evidence": [ev],
                "match": {"method": "exact", "matched_uid": m.uid, "score": 1.0, "candidates": []},
                "duplicate_warning": False,
                "merge_into_uid": None,
                "ner_label": None,
            }
        else:
            candidates, is_dup = matching.match_surface(m.text, catalog_entries, index)
            props = {}
            cat = cat_objs.get(category_slug)
            if cat:
                ident = graph_service.searchable_property_names(cat)[0]
                props[ident] = m.text
            best = candidates[0] if candidates else None
            node = {
                "kind": "new",
                "category_slug": category_slug,
                "category_name": cat_name.get(category_slug),
                "uid": None,
                "display": m.text,
                "properties": props,
                "evidence": [ev],
                "match": ({
                    "method": best["method"], "matched_uid": best["uid"],
                    "score": best["score"], "candidates": candidates,
                } if best else None),
                "duplicate_warning": is_dup,
                "merge_into_uid": None,
                "ner_label": m.ner_label,
            }
        nodes_by_key[key] = node

    # deterministic temp_ids by first-occurrence order
    key_temp = {}
    for i, key in enumerate(key_order, start=1):
        key_temp[key] = f"n{i}"
        nodes_by_key[key]["temp_id"] = f"n{i}"

    # relationship candidates
    mention_nodes = []
    for m in mentions:
        node = nodes_by_key[mention_key[id(m)]]
        if not node.get("category_slug"):
            continue
        mention_nodes.append({
            "node_key": mention_key[id(m)],
            "category_slug": node["category_slug"],
            "sent_index": m.sent_index,
            "start_char": m.start_char,
        })

    rel_candidates = relations.propose(
        mention_nodes, detection.sentence_forms, detection.sentences,
        include_existing=include_existing_nodes,
    )
    relationships = []
    for i, rc in enumerate(rel_candidates, start=1):
        from_temp = key_temp.get(rc["from_key"])
        to_temp = key_temp.get(rc["to_key"])
        if not from_temp or not to_temp:
            continue
        relationships.append({
            "temp_id": f"r{i}",
            "edge_type_slug": rc["edge_type_slug"],
            "rel_type": rc["rel_type"],
            "edge_type_name": rc["edge_type_name"],
            "from": from_temp,
            "to": to_temp,
            "properties": {},
            "evidence": [{"text": rc["evidence_text"], "sentence": rc["sent_index"]}],
            "trigger_matched": rc["trigger_matched"],
            "approval": "pending",
        })

    nodes = [nodes_by_key[k] for k in key_order]
    for node in nodes:
        node.setdefault("approval", "pending")

    proposal = {"nodes": nodes, "relationships": relationships}
    revalidate(proposal)
    for node in nodes:
        node["confidence"] = scoring.node_confidence(node)
    for rel in relationships:
        rel["confidence"] = scoring.relationship_confidence(rel)
    return proposal
