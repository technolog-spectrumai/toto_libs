from django.conf import settings
from neomodel import db

def is_neo4j_connected() -> bool:
    """
    Returns True if Neo4j is reachable.
    If settings.NEO4J_CHECK_ALIVE is False (or missing), assume it's alive without checking.
    """
    # Default: skip check for performance
    check_alive = getattr(settings, "NEO4J_CHECK_ALIVE", False)

    if not check_alive:
        return True

    try:
        db.cypher_query("RETURN 1")
        return True
    except Exception:
        return False
