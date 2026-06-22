"""Deterministic relationship proposal.

For each sentence, every ordered pair of detected entities is considered. A pair
only yields edges that **Bento would accept** (``graph_service.edge_types_between``
honors ``allowed_sources``/``allowed_targets``). Edge types that declare
``trigger_lemmas`` are proposed only when a trigger appears in the sentence; when
any triggered edge fires for a pair, it wins (higher precision) over bare
co-occurrence. No statistical/LLM inference — pure rules over the templates.
"""

from toto.bento import graph_service

# Bound the pairwise work per sentence (deterministic: first N by position).
MAX_ENTITIES_PER_SENTENCE = 15


def _edge_types_between_cached(cache, src_slug, dst_slug):
    key = (src_slug, dst_slug)
    if key not in cache:
        cache[key] = graph_service.edge_types_between(src_slug, dst_slug)
    return cache[key]


def propose(mention_nodes, sentence_forms, sentences):
    """Return a list of relationship-candidate dicts.

    ``mention_nodes``: dicts with ``node_key``, ``category_slug``, ``sent_index``,
    ``start_char`` (only entities resolved to a category participate).
    ``sentence_forms``: per-sentence sets of lemma/orth forms (trigger matching).
    """
    by_sent = {}
    for m in mention_nodes:
        if not m.get("category_slug"):
            continue
        by_sent.setdefault(m["sent_index"], []).append(m)

    cache = {}
    results = []
    seen = set()

    for sent_index, ents in by_sent.items():
        ents = sorted(ents, key=lambda m: m["start_char"])[:MAX_ENTITIES_PER_SENTENCE]
        forms = sentence_forms[sent_index] if sent_index < len(sentence_forms) else set()
        evidence_text = sentences[sent_index] if sent_index < len(sentences) else ""

        for i in range(len(ents)):
            for j in range(i + 1, len(ents)):
                a, b = ents[i], ents[j]
                if a["node_key"] == b["node_key"]:
                    continue

                proposed = []  # (src, dst, edge_type, trigger_matched)
                for src, dst in ((a, b), (b, a)):
                    for et in _edge_types_between_cached(
                        cache, src["category_slug"], dst["category_slug"]
                    ):
                        triggers = {str(t).lower() for t in (et.trigger_lemmas or []) if t}
                        if triggers:
                            if triggers & forms:
                                proposed.append((src, dst, et, True))
                        else:
                            proposed.append((src, dst, et, False))

                triggered = [p for p in proposed if p[3]]
                # Triggered edges win; otherwise keep bare co-occurrence in text
                # order only (src == a) to avoid proposing both directions.
                chosen = triggered if triggered else [p for p in proposed if p[0] is a]

                for src, dst, et, trig in chosen:
                    dedup = (src["node_key"], dst["node_key"], et.slug)
                    if dedup in seen:
                        continue
                    seen.add(dedup)
                    results.append({
                        "from_key": src["node_key"],
                        "to_key": dst["node_key"],
                        "edge_type_slug": et.slug,
                        "rel_type": et.rel_type,
                        "edge_type_name": et.name,
                        "sent_index": sent_index,
                        "evidence_text": evidence_text,
                        "trigger_matched": trig,
                    })

    return results
