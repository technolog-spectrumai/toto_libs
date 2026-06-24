"""Owns the neomodel connection for the whole platform.

ravioli is the sole Neo4j boundary. Raw-Cypher callers use :class:`Neo4jClient`;
the typed/object layer (currently only ``bento``) uses ``neomodel``. Both must
point at the *same* database, so the neomodel connection URL is configured here,
once, from the same ``NEO4J_*`` settings ``Neo4jClient`` reads. No other app
should set ``neomodel.config.DATABASE_URL``.
"""

from urllib.parse import quote, urlparse

from django.conf import settings

from .connection import connection_uris, is_enabled


class Neo4jDisabled(RuntimeError):
    """Raised when neomodel access is requested but RAVIOLI_ENABLED is False."""


_configured_url = None
_configured_for = None  # the (uri, user, password) tuple _configured_url was built for


def _reachable_base_uri():
    """Return the first candidate Bolt URI neomodel can actually reach.

    :func:`connection.connection_uris` returns endpoints in *try-each-in-order*
    preference — with the dev ``127.0.0.1`` local fallback first — and
    :class:`Neo4jClient` walks them until one connects. neomodel, however, accepts
    only a single URL with no fallback of its own. Blindly taking the first
    candidate would pin neomodel to ``127.0.0.1``: correct for host-based dev, but
    wrong inside a container, where the compose ``neo4j`` host resolves and
    loopback has nothing — so every neomodel (bento) write would fail with a
    ``127.0.0.1:7687`` connection-refused even though ``NEO4J_URI`` points at the
    in-stack database.

    So we probe — reusing :class:`Neo4jClient`'s connect-and-verify path — and
    return the endpoint that actually connects, falling back to the first
    candidate when none do (lets the self-heal retry / "graph unavailable" banner
    surface the failure rather than masking it).
    """
    candidates = connection_uris(settings.NEO4J_URI)
    if len(candidates) == 1:
        return candidates[0]

    from .connection import Neo4jClient, Neo4jConnectionError

    client = Neo4jClient()
    try:
        client.driver()  # connects to the first reachable candidate + verifies it
        return client._driver_uri or candidates[0]
    except Neo4jConnectionError:
        return candidates[0]
    finally:
        client.close()


def _bolt_url_with_credentials():
    """Build a ``bolt://user:pass@host:port`` URL for neomodel from settings,
    targeting a *reachable* endpoint (see :func:`_reachable_base_uri`)."""
    parsed = urlparse(_reachable_base_uri())
    user = quote(str(settings.NEO4J_USER), safe="")
    password = quote(str(settings.NEO4J_PASSWORD), safe="")
    host = parsed.hostname or "localhost"
    port = parsed.port or 7687
    scheme = parsed.scheme or "bolt"
    return f"{scheme}://{user}:{password}@{host}:{port}"


def ensure_configured(force=False):
    """Point neomodel at the platform Neo4j. Idempotent.

    Returns the configured bolt URL. Raises :class:`Neo4jDisabled` when
    ``RAVIOLI_ENABLED`` is False so callers can render a graceful
    "graph unavailable" state instead of crashing.

    The reachable-endpoint probe (see :func:`_reachable_base_uri`) opens a
    connection, so it runs only when neomodel is first configured, when the
    ``NEO4J_*`` settings change, or on ``force`` (the self-heal retry) — never on
    neomodel's hot path, which returns the cached URL.
    """
    global _configured_url, _configured_for
    if not is_enabled():
        raise Neo4jDisabled("RAVIOLI_ENABLED is False — Neo4j is not available.")

    from neomodel import config as neo_config

    target = (settings.NEO4J_URI, str(settings.NEO4J_USER), str(settings.NEO4J_PASSWORD))
    if _configured_url is not None and not force and _configured_for == target:
        return _configured_url

    url = _bolt_url_with_credentials()
    if force or _configured_url != url:
        neo_config.DATABASE_URL = url
        # If a driver was already built against a stale URL, force a rebuild so
        # the new host/credentials take effect.
        if force:
            try:
                from neomodel import db as neo_db

                neo_db.set_connection(url=url)
            except Exception:  # pragma: no cover - older/newer neomodel APIs
                pass
    _configured_url = url
    _configured_for = target
    return url
