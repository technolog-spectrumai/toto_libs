"""Neo4j search service — the only place that runs search Cypher queries."""

from ..connection import Neo4jClient, Neo4jConnectionError

# ---------------------------------------------------------------------------
# Mode constants
# ---------------------------------------------------------------------------

MODE_KEYWORD = "keyword"
MODE_FULLTEXT = "fulltext"
MODE_SEMANTIC = "semantic"

# Canonical modes + legacy aliases accepted as valid input
_KNOWN_MODES = {
    MODE_KEYWORD, MODE_FULLTEXT, MODE_SEMANTIC,
    "basic", "advanced", "deep",
}

# Legacy aliases → canonical display name
_CANONICAL_DISPLAY = {
    "basic":    MODE_KEYWORD,
    "advanced": MODE_KEYWORD,
    "deep":     MODE_FULLTEXT,
}


def resolve_mode(mode_str: str) -> str:
    """Normalize any mode string to a known mode. Unknown input → MODE_KEYWORD."""
    m = str(mode_str or "").lower().strip()
    return m if m in _KNOWN_MODES else MODE_KEYWORD


def canonical_mode(mode: str) -> str:
    """Map legacy aliases to canonical display names; canonical names pass through."""
    return _CANONICAL_DISPLAY.get(mode, mode)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SearchUnavailableError(Exception):
    """Raised when Neo4j is unreachable during a search."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _client():
    return Neo4jClient()


def _to_json_safe(v):
    """Recursively convert Neo4j driver values to JSON-serializable Python types.

    The driver can return temporal types (DateTime, Date, Time, Duration) and
    spatial types (Point) that are not handled by the standard json module or
    DjangoJSONEncoder.  Converting them to str/float is safe enough for display.
    """
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, list):
        return [_to_json_safe(i) for i in v]
    if isinstance(v, dict):
        return {k: _to_json_safe(val) for k, val in v.items()}
    # neo4j temporal: DateTime, Date, Time, Duration → ISO string via str()
    # neo4j spatial: Point → "POINT(x y)" via str()
    return str(v)


def _record_to_dict(record, score_key=None):
    """Convert a raw neo4j Record into a plain JSON-safe dict."""
    node = record.get("n") or record.get("node")
    labels = list(record.get("labels") or (node.labels if node else []))
    raw_props = dict(node) if node else {}
    props = {k: _to_json_safe(v) for k, v in raw_props.items()}
    result = {"labels": labels, "props": props}
    if score_key and record.get(score_key) is not None:
        result["score"] = round(float(record[score_key]), 4)
    return result


def _normalize_result(r: dict) -> dict:
    """Ensure every result has the canonical shape: labels, props, score."""
    return {
        "labels": list(r.get("labels") or []),
        "props": r.get("props") or {},
        "score": r.get("score", None),
    }


# ---------------------------------------------------------------------------
# Primitive search functions (unchanged from original)
# ---------------------------------------------------------------------------


def basic_search(q, limit=25):
    """Search nodes whose `text` field contains *q* (case-insensitive)."""
    try:
        records = _client().run_cypher(
            """
            MATCH (n)
            WHERE toLower(coalesce(n.text, "")) CONTAINS toLower($q)
            RETURN labels(n) AS labels, n
            LIMIT $limit
            """,
            {"q": q, "limit": int(limit)},
        )
    except Neo4jConnectionError as exc:
        raise SearchUnavailableError(str(exc)) from exc
    return [_record_to_dict(r) for r in records]


def advanced_search(q, limit=25):
    """Search nodes whose `name`, `description`, or `text` contains *q*."""
    try:
        records = _client().run_cypher(
            """
            MATCH (n)
            WHERE toLower(coalesce(n.name, ""))        CONTAINS toLower($q)
               OR toLower(coalesce(n.description, "")) CONTAINS toLower($q)
               OR toLower(coalesce(n.text, ""))        CONTAINS toLower($q)
            RETURN labels(n) AS labels, n
            LIMIT $limit
            """,
            {"q": q, "limit": int(limit)},
        )
    except Neo4jConnectionError as exc:
        raise SearchUnavailableError(str(exc)) from exc
    return [_record_to_dict(r) for r in records]


def ensure_fulltext_index():
    """Idempotent — creates the chunk full-text index if absent."""
    try:
        _client().run_cypher(
            """
            CREATE FULLTEXT INDEX chunk_text_index IF NOT EXISTS
            FOR (c:Chunk) ON EACH [c.text, c.title]
            """
        )
    except Neo4jConnectionError as exc:
        raise SearchUnavailableError(str(exc)) from exc


def deep_search(q, limit=25, exact=False):
    """Full-text index search on Chunk nodes (text + title).

    exact=True wraps q in Lucene phrase quotes for exact-phrase matching.
    """
    query_str = f'"{q}"' if exact else q
    try:
        ensure_fulltext_index()
        records = _client().run_cypher(
            """
            CALL db.index.fulltext.queryNodes("chunk_text_index", $q)
            YIELD node, score
            RETURN labels(node) AS labels, node, score
            LIMIT $limit
            """,
            {"q": query_str, "limit": int(limit)},
        )
    except Neo4jConnectionError as exc:
        raise SearchUnavailableError(str(exc)) from exc
    return [_record_to_dict(r, score_key="score") for r in records]


# ---------------------------------------------------------------------------
# Smart / unified search
# ---------------------------------------------------------------------------


def semantic_available() -> bool:
    """Return True when Vicuna embeddings are enabled (fast, no Ollama call)."""
    try:
        from toto.vicuna.embeddings import embeddings_enabled
        return embeddings_enabled()
    except Exception:
        return False


def run_search(q: str, mode: str = MODE_KEYWORD, limit: int = 25, exact: bool = False) -> dict:
    """Execute a search and return results with metadata.

    Returns a dict with keys:
        results          list[dict]  — normalized {labels, props, score}
        requested_mode   str         — canonical name of the user-requested mode
        effective_mode   str         — canonical name of the mode actually used
        fallback_used    bool        — True when semantic fell back to keyword
        fallback_reason  str|None    — why fallback happened (for staff/debug display)
        semantic_available bool      — whether embeddings are enabled
    """
    mode = resolve_mode(mode)
    requested_canonical = canonical_mode(mode)
    sem_avail = semantic_available()

    results: list[dict] = []
    effective_mode = requested_canonical
    fallback_used = False
    fallback_reason: str | None = None
    semantic_done = False  # True iff semantic succeeded

    # --- Try the semantic (vector) path only when explicitly requested ---
    if mode == MODE_SEMANTIC:
        try:
            from toto.ravioli.vector_search import (
                VectorSearchUnavailable,
                retrieve_vector_results,
            )
            results = retrieve_vector_results(q, top_k=limit)
            effective_mode = MODE_SEMANTIC
            semantic_done = True
        except Exception as exc:
            fallback_used = True
            fallback_reason = str(exc)

    # --- Keyword / fulltext path (primary for non-semantic, fallback otherwise) ---
    if not semantic_done:
        if mode in ("deep", MODE_FULLTEXT):
            raw = deep_search(q, limit=limit, exact=exact)
            effective_mode = MODE_FULLTEXT
        elif mode == "basic":
            raw = basic_search(q, limit=limit)
            effective_mode = MODE_KEYWORD
        else:
            # covers: keyword, advanced, semantic (fallback)
            raw = advanced_search(q, limit=limit)
            effective_mode = MODE_KEYWORD
        results = [_normalize_result(r) for r in raw]
    else:
        results = [_normalize_result(r) for r in results]

    return {
        "results": results,
        "requested_mode": requested_canonical,
        "effective_mode": effective_mode,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "semantic_available": sem_avail,
    }
