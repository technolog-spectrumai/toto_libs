"""Deterministic relationship proposal.

For each sentence, every ordered pair of detected entities is considered. A pair
only yields edges that **Bento would accept** (``graph_service.edge_types_between``
honors ``allowed_sources``/``allowed_targets``). Edge types that declare
``trigger_lemmas`` are proposed only when a trigger appears in the sentence; when
any triggered edge fires for a pair, it wins (higher precision) over bare
co-occurrence. No statistical/LLM inference — pure rules over the templates.

When ``include_existing`` is set, a second whole-text pass also links any mention
to **existing** graph-node mentions that appear in *other* sentences (same-sentence
pairs are already covered by the per-sentence pass), so a new entity can connect to
an existing node named elsewhere in the pasted text.
"""

from toto.bento import graph_service

# Bound the pairwise work per sentence (deterministic: first N by position).
MAX_ENTITIES_PER_SENTENCE = 15


def _edge_types_between_cached(cache, src_slug, dst_slug):
    key = (src_slug, dst_slug)
    if key not in cache:
        cache[key] = graph_service.edge_types_between(src_slug, dst_slug)
    return cache[key]


def _forms_at(sentence_forms, idx):
    return sentence_forms[idx] if 0 <= idx < len(sentence_forms) else set()


def _chosen_for_pair(cache, a, b, forms, *, order_restricted=True):
    """Bento-valid ``(src, dst, edge_type, trigger_matched)`` for the unordered pair.

    Triggered edges win (higher precision). Otherwise bare co-occurrence is kept
    in text order only (``src is a``) within a sentence to avoid proposing both
    directions of a symmetric type; the whole-text pass passes
    ``order_restricted=False`` (no meaningful single order across sentences) so
    every Bento-valid direction is proposed and the directional one isn't dropped.
    """
    proposed = []
    for src, dst in ((a, b), (b, a)):
        for et in _edge_types_between_cached(cache, src["category_slug"], dst["category_slug"]):
            triggers = {str(t).lower() for t in (et.trigger_lemmas or []) if t}
            if triggers:
                if triggers & forms:
                    proposed.append((src, dst, et, True))
            else:
                proposed.append((src, dst, et, False))
    triggered = [p for p in proposed if p[3]]
    if triggered:
        return triggered
    return [p for p in proposed if p[0] is a] if order_restricted else proposed


def propose(mention_nodes, sentence_forms, sentences, *, include_existing=False):
    """Return a list of relationship-candidate dicts.

    ``mention_nodes``: dicts with ``node_key``, ``category_slug``, ``sent_index``,
    ``start_char`` (only entities resolved to a category participate).
    ``sentence_forms``: per-sentence sets of lemma/orth forms (trigger matching).
    ``include_existing``: also pair across sentences with existing-node mentions.
    """
    by_sent = {}
    for m in mention_nodes:
        if not m.get("category_slug"):
            continue
        by_sent.setdefault(m["sent_index"], []).append(m)

    cache = {}
    results = []
    seen = set()

    def _emit(chosen, sent_index, evidence_text):
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

    # ── per-sentence co-occurrence ──────────────────────────────────────────
    for sent_index, ents in by_sent.items():
        ents = sorted(ents, key=lambda m: m["start_char"])[:MAX_ENTITIES_PER_SENTENCE]
        forms = _forms_at(sentence_forms, sent_index)
        evidence_text = sentences[sent_index] if sent_index < len(sentences) else ""
        for i in range(len(ents)):
            for j in range(i + 1, len(ents)):
                a, b = ents[i], ents[j]
                if a["node_key"] == b["node_key"]:
                    continue
                _emit(_chosen_for_pair(cache, a, b, forms), sent_index, evidence_text)

    # ── whole-text: link to existing nodes named in *other* sentences ───────
    if include_existing:
        existing, seen_existing = [], set()
        for m in mention_nodes:
            if (
                m.get("category_slug")
                and m["node_key"][0] == "existing"
                and m["node_key"] not in seen_existing
            ):
                seen_existing.add(m["node_key"])
                existing.append(m)
        existing = existing[:MAX_ENTITIES_PER_SENTENCE]

        for m in mention_nodes:
            if not m.get("category_slug"):
                continue
            for e in existing:
                if m["node_key"] == e["node_key"] or m["sent_index"] == e["sent_index"]:
                    continue  # same-sentence pairs handled by the per-sentence pass
                forms = _forms_at(sentence_forms, m["sent_index"]) | _forms_at(sentence_forms, e["sent_index"])
                evidence_text = " […] ".join(
                    sentences[idx] for idx in (m["sent_index"], e["sent_index"])
                    if idx < len(sentences)
                )
                _emit(
                    _chosen_for_pair(cache, m, e, forms, order_restricted=False),
                    m["sent_index"],
                    evidence_text,
                )

    return results
