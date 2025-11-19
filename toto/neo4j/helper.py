from neomodel import config, db
from neo4j import GraphDatabase
import math
from django.conf import settings


class Neo4jHelper:
    """
    Helper class for Neo4j operations.
    Uses Django settings for configuration.
    """

    def __init__(self):

        # Driver requires auth tuple
        self.driver = GraphDatabase.driver(settings.NEOMODEL_NEO4J_BOLT_URL)

    def flush_db_batch(self, batch_size: int = 1000, max_nodes: int | None = None) -> int:
        """
        Deletes nodes and relationships in batches of `batch_size`.
        """
        with self.driver.session() as session:
            result = session.run("MATCH (n) RETURN count(n) AS total")
            total_nodes = result.single()["total"]

            target_nodes = min(total_nodes, max_nodes) if max_nodes else total_nodes
            num_batches = math.ceil(target_nodes / batch_size)

            total_deleted = 0
            for _ in range(num_batches):
                res = session.run(f"""
                    MATCH (n)
                    WITH n LIMIT {batch_size}
                    DETACH DELETE n
                    RETURN count(n) AS deleted
                """)
                deleted = res.single()["deleted"]
                total_deleted += deleted
                if deleted == 0:
                    break

            return total_deleted

    def close(self) -> None:
        self.driver.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
