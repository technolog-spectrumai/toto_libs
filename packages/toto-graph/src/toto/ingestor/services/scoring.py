"""Deterministic confidence scoring (pure formulas over detection signals)."""


def _clamp(x):
    return max(0.0, min(1.0, round(float(x), 4)))


def node_confidence(node):
    """Confidence for a proposed/reference node.

    existing exact match → 1.0; existing via fuzzy → the match score; a new
    entity → a base by how well it was categorized (NER label mapped vs not).
    """
    if node["kind"] == "existing":
        match = node.get("match") or {}
        if match.get("method") == "exact" or not match:
            return 1.0
        return _clamp(match.get("score", 0.8))
    # new entity
    base = 0.55 if node.get("category_slug") else 0.35
    if node.get("ner_label"):
        base += 0.1
    if node.get("duplicate_warning"):
        base -= 0.1  # likely a duplicate of an existing node — needs user merge
    return _clamp(base)


def relationship_confidence(rel):
    """Confidence for a proposed relationship.

    co-occurrence base; a matched trigger lemma is a strong signal.
    """
    base = 0.4
    if rel.get("trigger_matched"):
        base += 0.35
    return _clamp(base)
