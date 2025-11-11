from neomodel import config, db
from neo4j import GraphDatabase
import math
from django.conf import settings


def is_connected() -> bool:
    """
    Returns True if Neo4j is reachable.
    If settings.NEO4J_CHECK_ALIVE is False (or missing), assume it's alive without checking.
    """
    check_alive = getattr(settings, "NEO4J_CHECK_ALIVE", False)
    if not check_alive:
        return True

    try:
        db.cypher_query("RETURN 1")
        return True
    except Exception as e:
        return False