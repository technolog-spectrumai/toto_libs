from django.conf import settings
from neomodel import db
from neo4j import GraphDatabase
import math


class Neo4jHelper:
    """
    Helper class for Neo4j operations.
    Uses Django settings for configuration.
    """

    def __init__(self):
        # Build driver from DATABASE_URL in settings
        self.driver = GraphDatabase.driver(settings.DATABASE_URL)

    def is_connected(self) -> bool:
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
        except Exception:
            return False

    def flush_db_batch(self, batch_size: int = 1000, max_nodes: int | None = None) -> int:
        """
        Deletes nodes and relationships in batches of `batch_size`.
        Calculates total nodes first, then number of batches.
        Stops after deleting `max_nodes` if provided.
        Returns the total number of nodes deleted.
        """
        with self.driver.session() as session:
            # Step 1: count total nodes
            result = session.run("MATCH (n) RETURN count(n) AS total")
            total_nodes = result.single()["total"]

            # Step 2: cap by max_nodes if provided
            target_nodes = min(total_nodes, max_nodes) if max_nodes else total_nodes

            # Step 3: calculate number of batches
            num_batches = math.ceil(target_nodes / batch_size)

            total_deleted = 0

            # Step 4: run bounded loop
            for _ in range(num_batches):
                res = session.run(f"""
                    MATCH (n)
                    WITH n LIMIT {batch_size}
                    DETACH DELETE n
                    RETURN count(n) AS deleted
                """)
                deleted = res.single()["deleted"]
                total_deleted += deleted

                if deleted == 0:  # nothing left
                    break

            return total_deleted

    def close(self) -> None:
        """Close the Neo4j driver connection."""
        self.driver.close()

    def __enter__(self):
        # Return the helper instance when entering the context
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Ensure driver is closed when exiting the context
        self.close()


def is_neo4j_connected():
    return Neo4jHelper().is_connected()