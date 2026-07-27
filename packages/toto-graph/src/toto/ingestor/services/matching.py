"""Duplicate / fuzzy matching of a surface against the existing-entity catalog.

Uses rapidfuzz when available (token-sort ratio, robust to word order), and
falls back to the stdlib :mod:`difflib` so the pipeline still runs without the
dependency. Scores are normalized to 0..1 and ordering is deterministic.
"""

from collections import defaultdict

from . import config
from .text import normalize


def _scorer():
    """Return ``(score_fn, is_rapidfuzz)`` where score_fn(a, b) -> 0..100."""
    try:
        from rapidfuzz import fuzz
        return fuzz.token_sort_ratio, True
    except ImportError:
        import difflib

        def _ratio(a, b):
            return difflib.SequenceMatcher(None, a, b).ratio() * 100.0

        return _ratio, False


def build_index(catalog_entries):
    """Group catalog entries by normalized surface for O(1) exact lookup."""
    index = defaultdict(list)
    for e in catalog_entries:
        index[e.normalized].append(e)
    return index


def _candidate(entry, score, method):
    return {
        "uid": entry.uid,
        "display": entry.display,
        "category_slug": entry.category_slug,
        "surface": entry.surface,
        "score": round(float(score), 4),
        "method": method,
    }


def match_surface(surface, catalog_entries, index=None, limit=5):
    """Return ``(candidates, is_duplicate)`` for a surface form.

    ``candidates`` are sorted best-first (capped at ``limit``); ``is_duplicate``
    is True when the best score is at/above the duplicate threshold. Exact
    normalized matches short-circuit at score 1.0.
    """
    norm = normalize(surface)
    if not norm:
        return [], False
    index = index if index is not None else build_index(catalog_entries)

    exact = index.get(norm)
    if exact:
        cands = [_candidate(e, 1.0, "exact") for e in exact[:limit]]
        return cands, True

    score_fn, _ = _scorer()
    cand_threshold = config.candidate_threshold()
    dup_threshold = config.dup_threshold()

    # Score one representative per distinct surface to bound work, then keep the
    # best-scoring candidates above the candidate threshold.
    scored = []
    for entry in catalog_entries[: config.fuzzy_max_candidates()]:
        ratio = score_fn(norm, entry.normalized) / 100.0
        if ratio >= cand_threshold:
            scored.append((ratio, entry))
    # Deterministic ordering: score desc, then surface, then uid.
    scored.sort(key=lambda t: (-t[0], t[1].normalized, t[1].uid))

    candidates = [_candidate(e, s, "fuzzy") for s, e in scored[:limit]]
    is_duplicate = bool(candidates) and candidates[0]["score"] >= dup_threshold
    return candidates, is_duplicate
