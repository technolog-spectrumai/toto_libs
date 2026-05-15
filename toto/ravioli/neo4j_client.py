from neo4j import GraphDatabase
from django.conf import settings


class Neo4jClient:
    def __init__(self, uri=None, user=None, password=None):
        self.driver = GraphDatabase.driver(
            uri or settings.NEO4J_URI,
            auth=(user or settings.NEO4J_USER, password or settings.NEO4J_PASSWORD),
        )

    def close(self):
        self.driver.close()

    # ---------------------------------------------------------
    # GENERIC CYPHER RUNNER (SAFE, MATERIALIZED)
    # ---------------------------------------------------------
    def run_cypher(self, query, params=None):
        with self.driver.session() as session:
            result = session.run(query, params or {})
            return list(result)   # <-- ALWAYS RETURN A LIST

    @staticmethod
    def handle_value(v, nodes, edges):
        # Single node
        if hasattr(v, "labels"):
            nodes[str(v.id)] = {
                "id": str(v.id),
                "labels": list(v.labels),
                "props": dict(v),
            }
        # Single relationship
        elif hasattr(v, "type"):
            edges.append({
                "id": f"rel-{v.id}",  # ensure unique edge id
                "type": v.type,
                "start": str(v.start_node.id),
                "end": str(v.end_node.id),
                "props": dict(v),
            })

    def extract_graph(self, records):
        nodes = {}
        edges = []

        for record in records:
            for key, value in record.items():
                # If it's a list (nodes or relationships or mixed)
                if isinstance(value, list):
                    for v in value:
                        self.handle_value(v, nodes, edges)
                else:
                    self.handle_value(value, nodes, edges)

        return list(nodes.values()), edges

    # ---------------------------------------------------------
    # FETCH SINGLE NODE
    # ---------------------------------------------------------
    def get_node_by_id(self, node_id):
        query = """
        MATCH (n)
        WHERE id(n) = $id
        RETURN n
        """
        records = self.run_cypher(query, {"id": int(node_id)})

        if not records:
            return None

        n = records[0]["n"]

        return {
            "id": str(n.id),
            "labels": list(n.labels),
            "props": dict(n),
        }

    # ---------------------------------------------------------
    # FETCH SINGLE RELATIONSHIP
    # ---------------------------------------------------------
    def get_relation_by_id(self, rel_id):
        if not rel_id or rel_id == "None":
            return None

        try:
            rel_id = int(rel_id)
        except (TypeError, ValueError):
            return None

        query = """
        MATCH (a)-[r]->(b)
        WHERE id(r) = $id
        RETURN id(r) AS id,
               type(r) AS type,
               properties(r) AS props,
               id(a) AS start_id,
               id(b) AS end_id
        """
        records = self.run_cypher(query, {"id": rel_id})
        return records[0] if records else None

    def update_node(self, node_id, props):
        query = """
        MATCH (n)
        WHERE id(n) = $id
        SET n = $props
        RETURN n
        """
        return self.run_cypher(query, {"id": int(node_id), "props": props})

    def update_relation(self, rel_id, props):
        query = """
        MATCH ()-[r]->()
        WHERE id(r) = $id
        SET r = $props
        RETURN r
        """
        return self.run_cypher(query, {"id": int(rel_id), "props": props})

    def delete_node(self, node_id):
        query = """
        MATCH (n)
        WHERE id(n) = $id
        DETACH DELETE n
        """
        return self.run_cypher(query, {"id": int(node_id)})

    def delete_relation(self, rel_id):
        query = """
        MATCH ()-[r]->()
        WHERE id(r) = $id
        DELETE r
        """
        return self.run_cypher(query, {"id": int(rel_id)})

    def create_node(self, labels, props):
        query = f"""
        CREATE (n:{':'.join(labels)} $props)
        RETURN id(n) AS id
        """
        records = self.run_cypher(query, {"props": props})
        return records[0]["id"] if records else None

    def create_relation(self, start_id, end_id, rel_type, props):
        query = f"""
        MATCH (a), (b)
        WHERE id(a) = $start AND id(b) = $end
        CREATE (a)-[r:{rel_type} $props]->(b)
        RETURN id(r) AS id
        """
        params = {
            "start": int(start_id),
            "end": int(end_id),
            "props": props,
        }
        records = self.run_cypher(query, params)
        return records[0]["id"] if records else None

    def find_relation(self, start_id, end_id, rel_type):
        query = f"""
        MATCH (a)-[r:{rel_type}]->(b)
        WHERE id(a) = $start AND id(b) = $end
        RETURN id(r) AS id, properties(r) AS props,
               id(a) AS start_id, id(b) AS end_id,
               type(r) AS type
        """
        records = self.run_cypher(query, {"start": int(start_id), "end": int(end_id)})
        return records[0] if records else None

    def find_all_relations(self, start_id, end_id):
        start_id = int(start_id)
        end_id = int(end_id)

        query = """
        MATCH (a)-[r]->(b)
        WHERE id(a) = $start AND id(b) = $end
        RETURN id(r) AS id, properties(r) AS props,
               id(a) AS start_id, id(b) AS end_id,
               type(r) AS type
        """
        return self.run_cypher(query, {"start": start_id, "end": end_id})

    def get_subgraph_from_root(self, root_id, depth=5):
        root_id = int(root_id)

        query = f"""
        MATCH (root)
        WHERE id(root) = $root_id
        MATCH p = (root)-[*0..{depth}]-(n)
        WITH collect(DISTINCT n) AS nodes,
             collect(DISTINCT relationships(p)) AS rels
        RETURN nodes,
               reduce(r = [], x IN rels | r + x) AS relationships
        """

        print("Running subgraph query with root_id =", root_id)

        records = self.run_cypher(query, {"root_id": root_id})

        print("Raw records:", records)

        nodes, edges = self.extract_graph(records)

        print("Extracted nodes:", nodes)
        print("Extracted edges:", edges)

        return query, nodes, edges


