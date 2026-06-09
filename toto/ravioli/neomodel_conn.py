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


def _bolt_url_with_credentials():
    """Build a ``bolt://user:pass@host:port`` URL for neomodel from settings.

    Reuses :func:`connection.connection_uris` so the dev 127.0.0.1 local
    fallback ordering matches what ``Neo4jClient`` uses. neomodel only accepts a
    single URL, so we take the preferred (first) candidate.
    """
    base = connection_uris(settings.NEO4J_URI)[0]
    parsed = urlparse(base)
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
    """
    global _configured_url
    if not is_enabled():
        raise Neo4jDisabled("RAVIOLI_ENABLED is False — Neo4j is not available.")

    from neomodel import config as neo_config

    url = _bolt_url_with_credentials()
    if force or _configured_url != url:
        neo_config.DATABASE_URL = url
        # If a driver was already built against a stale URL, force a rebuild so
        # the new credentials/host take effect.
        if force:
            try:
                from neomodel import db as neo_db

                neo_db.set_connection(url=url)
            except Exception:  # pragma: no cover - older/newer neomodel APIs
                pass
        _configured_url = url
    return url
