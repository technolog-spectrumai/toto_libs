from django.conf import settings
from faker import Faker
from oya.ingress import IngressCommand
from rest_framework.reverse import reverse_lazy

from ravioli.models import (
    CypherQuery,
    Graph,
    CollectionType,
    DataNode,
    RelationType,
    DataEdge,
)

fake = Faker()


class Command(IngressCommand):
    help = "Creates a demo Bolt user with read-only permissions and a default Cypher query + sample graph data"

    def process(self):
        # 📊 Dashboard block
        self.create_dashboard_item(
            title="Graph Database",
            icon="fa-solid fa-database",
            description="Creates one read-only Bolt user for demo purposes.",
            link=reverse_lazy("ravioli:graph"),
            public=False,
        )
        if not self.full:
            return

        # 🧩 Predefined Cypher query: show all nodes and edges
        cypher = """
        MATCH (n)-[r]->(m)
        RETURN n, r, m
        LIMIT 500
        """
        CypherQuery.objects.update_or_create(
            name="All Nodes and Edges",
            defaults={
                "description": "Fetch all nodes and relationships in the graph (limited to 500).",
                "query": cypher.strip(),
                "is_active": True,
            },
        )

        # 🗂️ Create a demo Graph
        graph, _ = Graph.objects.get_or_create(
            name="Demo Graph",
            defaults={"description": "A sample graph seeded with fake data."},
        )

        # 📦 Create some CollectionTypes
        person_type, _ = CollectionType.objects.get_or_create(
            name="Person",
            defaults={"json_schema": {"type": "object"}, "form_layout": {}},
        )
        company_type, _ = CollectionType.objects.get_or_create(
            name="Company",
            defaults={"json_schema": {"type": "object"}, "form_layout": {}},
        )

        # 👤 Create demo nodes
        alice = DataNode.objects.create(
            name=fake.first_name(),
            data={"age": fake.random_int(min=20, max=60)},
            collection_type=person_type,
            graph=graph,
        )
        bob = DataNode.objects.create(
            name=fake.first_name(),
            data={"age": fake.random_int(min=20, max=60)},
            collection_type=person_type,
            graph=graph,
        )
        acme = DataNode.objects.create(
            name=fake.company(),
            data={"industry": fake.bs()},
            collection_type=company_type,
            graph=graph,
        )

        # 🔗 Create relation types
        works_at, _ = RelationType.objects.get_or_create(
            name="WORKS_AT",
            defaults={"json_schema": {"type": "object"}, "form_layout": {}},
        )
        knows, _ = RelationType.objects.get_or_create(
            name="KNOWS",
            defaults={"json_schema": {"type": "object"}, "form_layout": {}},
        )

        # ➡️ Create edges
        DataEdge.objects.get_or_create(
            source=alice,
            target=acme,
            relation_type=works_at,
            graph=graph,
            defaults={"label": "employee", "metadata": {"since": fake.year()}},
        )
        DataEdge.objects.get_or_create(
            source=alice,
            target=bob,
            relation_type=knows,
            graph=graph,
            defaults={"label": "friend", "metadata": {"met": fake.city()}},
        )
