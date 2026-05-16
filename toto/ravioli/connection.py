from django.conf import settings


def is_enabled():
    return bool(getattr(settings, "RAVIOLI_ENABLED", False))


class Neo4jClient:
    """Wraps the Neo4j driver. neo4j is imported here and nowhere else."""

    def __init__(self, uri=None, user=None, password=None):
        from neo4j import GraphDatabase  # lazy — only when actually used
        self._driver = GraphDatabase.driver(
            uri or settings.NEO4J_URI,
            auth=(
                user or settings.NEO4J_USER,
                password or settings.NEO4J_PASSWORD,
            ),
        )

    def close(self):
        self._driver.close()

    # ------------------------------------------------------------------
    # GENERIC CYPHER
    # ------------------------------------------------------------------

    def run_cypher(self, query, params=None):
        with self._driver.session() as session:
            result = session.run(query, params or {})
            return list(result)

    # ------------------------------------------------------------------
    # GRAPH EXTRACTION (nodes + edges from raw records)
    # ------------------------------------------------------------------

    @staticmethod
    def _handle_value(v, nodes, edges):
        if hasattr(v, "labels"):
            nodes[str(v.id)] = {
                "id": str(v.id),
                "labels": list(v.labels),
                "props": dict(v),
            }
        elif hasattr(v, "type"):
            edges.append({
                "id": f"rel-{v.id}",
                "type": v.type,
                "start": str(v.start_node.id),
                "end": str(v.end_node.id),
                "props": dict(v),
            })

    def extract_graph(self, records):
        nodes = {}
        edges = []
        for record in records:
            for value in record.values():
                if isinstance(value, list):
                    for v in value:
                        self._handle_value(v, nodes, edges)
                else:
                    self._handle_value(value, nodes, edges)
        return list(nodes.values()), edges

    # ------------------------------------------------------------------
    # NODE / RELATIONSHIP HELPERS  (used by admin / query views)
    # ------------------------------------------------------------------

    def get_node_by_id(self, node_id):
        records = self.run_cypher(
            "MATCH (n) WHERE id(n) = $id RETURN n",
            {"id": int(node_id)},
        )
        if not records:
            return None
        n = records[0]["n"]
        return {"id": str(n.id), "labels": list(n.labels), "props": dict(n)}

    def get_relation_by_id(self, rel_id):
        if not rel_id or rel_id == "None":
            return None
        try:
            rel_id = int(rel_id)
        except (TypeError, ValueError):
            return None
        records = self.run_cypher(
            """
            MATCH (a)-[r]->(b)
            WHERE id(r) = $id
            RETURN id(r) AS id, type(r) AS type, properties(r) AS props,
                   id(a) AS start_id, id(b) AS end_id
            """,
            {"id": rel_id},
        )
        return records[0] if records else None

    def update_node(self, node_id, props):
        return self.run_cypher(
            "MATCH (n) WHERE id(n) = $id SET n = $props RETURN n",
            {"id": int(node_id), "props": props},
        )

    def update_relation(self, rel_id, props):
        return self.run_cypher(
            "MATCH ()-[r]->() WHERE id(r) = $id SET r = $props RETURN r",
            {"id": int(rel_id), "props": props},
        )

    def delete_node(self, node_id):
        return self.run_cypher(
            "MATCH (n) WHERE id(n) = $id DETACH DELETE n",
            {"id": int(node_id)},
        )

    def delete_relation(self, rel_id):
        return self.run_cypher(
            "MATCH ()-[r]->() WHERE id(r) = $id DELETE r",
            {"id": int(rel_id)},
        )

    def create_node(self, labels, props):
        query = f"CREATE (n:{':'.join(labels)} $props) RETURN id(n) AS id"
        records = self.run_cypher(query, {"props": props})
        return records[0]["id"] if records else None

    def create_relation(self, start_id, end_id, rel_type, props):
        query = f"""
        MATCH (a), (b)
        WHERE id(a) = $start AND id(b) = $end
        CREATE (a)-[r:{rel_type} $props]->(b)
        RETURN id(r) AS id
        """
        records = self.run_cypher(
            query,
            {"start": int(start_id), "end": int(end_id), "props": props},
        )
        return records[0]["id"] if records else None

    def find_relation(self, start_id, end_id, rel_type):
        query = f"""
        MATCH (a)-[r:{rel_type}]->(b)
        WHERE id(a) = $start AND id(b) = $end
        RETURN id(r) AS id, properties(r) AS props,
               id(a) AS start_id, id(b) AS end_id, type(r) AS type
        """
        records = self.run_cypher(query, {"start": int(start_id), "end": int(end_id)})
        return records[0] if records else None

    def find_all_relations(self, start_id, end_id):
        records = self.run_cypher(
            """
            MATCH (a)-[r]->(b)
            WHERE id(a) = $start AND id(b) = $end
            RETURN id(r) AS id, properties(r) AS props,
                   id(a) AS start_id, id(b) AS end_id, type(r) AS type
            """,
            {"start": int(start_id), "end": int(end_id)},
        )
        return records

    def get_subgraph_from_root(self, root_id, depth=5):
        root_id = int(root_id)
        query = f"""
        MATCH (root) WHERE id(root) = $root_id
        MATCH p = (root)-[*0..{depth}]-(n)
        WITH collect(DISTINCT n) AS nodes,
             collect(DISTINCT relationships(p)) AS rels
        RETURN nodes,
               reduce(r = [], x IN rels | r + x) AS relationships
        """
        records = self.run_cypher(query, {"root_id": root_id})
        nodes, edges = self.extract_graph(records)
        return query, nodes, edges
