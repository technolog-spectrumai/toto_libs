"""Neo4j search service — the only place that runs search Cypher queries."""

from ..connection import Neo4jClient, Neo4jConnectionError


class SearchUnavailableError(Exception):
    """Raised when Neo4j is unreachable during a search."""


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
