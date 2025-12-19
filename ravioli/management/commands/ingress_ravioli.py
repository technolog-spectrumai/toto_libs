from faker import Faker
from oya.ingress import IngressCommand
from rest_framework.reverse import reverse_lazy

from ravioli.models import (
    CypherQuery,
    Graph,
    CollectionType,
    DataNode,
    RelationType,
    DataEdge
)

fake = Faker()


class Command(IngressCommand):
    help = "Seeds a demo graph with sample nodes, relations, and a Cypher query"

    def process(self):
        # Dashboard block
        self.create_dashboard_item(
            title="Graph Database",
            icon="fa-solid fa-database",
            description="Demo graph with sample data",
            link=reverse_lazy("ravioli:graph"),
            public=False,
        )
        if not self.full:
            return

        # Cypher query
        CypherQuery.objects.update_or_create(
            name="All Nodes and Edges",
            defaults={
                "description": "Fetch all nodes and relationships (limit 500).",
                "query": "MATCH (n)-[r]->(m) RETURN n,r,m LIMIT 500",
                "is_active": True,
            },
        )

        # Graph
        graph, _ = Graph.objects.get_or_create(
            name="Demo Graph",
            defaults={"description": "Sample graph with fake data"},
        )

        # CollectionTypes with schema + layout
        person_type, _ = CollectionType.objects.get_or_create(
            name="Person",
            defaults={
                "json_schema": {
                    "type": "object",
                    "properties": {
                        "age": {"type": "integer"},
                        "email": {"type": "string"},
                        "active": {"type": "boolean"},
                    },
                    "required": ["age", "email"],
                },
                "form_layout": {
                    "fields": [
                        {"name": "age", "type": "integer", "label": "Age", "required": True},
                        {"name": "email", "type": "string", "label": "Email", "required": True},
                        {"name": "active", "type": "boolean", "label": "Is Active?", "required": False},
                    ]
                },
            },
        )

        company_type, _ = CollectionType.objects.get_or_create(
            name="Company",
            defaults={
                "json_schema": {
                    "type": "object",
                    "properties": {
                        "industry": {"type": "string"},
                        "founded": {"type": "integer"},
                    },
                    "required": ["industry"],
                },
                "form_layout": {
                    "fields": [
                        {"name": "industry", "type": "string", "label": "Industry", "required": True},
                        {"name": "founded", "type": "integer", "label": "Founded Year", "required": False},
                    ]
                },
            },
        )

        # Demo nodes
        alice = DataNode.objects.create(
            name=fake.first_name(),
            data={"age": fake.random_int(20, 60), "email": fake.email(), "active": True},
            collection_type=person_type,
            graph=graph,
        )
        bob = DataNode.objects.create(
            name=fake.first_name(),
            data={"age": fake.random_int(20, 60), "email": fake.email(), "active": False},
            collection_type=person_type,
            graph=graph,
        )
        acme = DataNode.objects.create(
            name=fake.company(),
            data={"industry": fake.bs(), "founded": int(fake.year())},
            collection_type=company_type,
            graph=graph,
        )

        # RelationTypes with metadata
        works_at, _ = RelationType.objects.get_or_create(
            name="WORKS_AT",
            defaults={
                "metadata": {
                    "properties": {
                        "since": {"type": "integer", "label": "Since Year"}
                    }
                }
            },
        )

        knows, _ = RelationType.objects.get_or_create(
            name="KNOWS",
            defaults={
                "metadata": {
                    "properties": {
                        "met": {"type": "string", "label": "Met At"}
                    }
                }
            },
        )

        # Demo edges
        DataEdge.objects.get_or_create(
            source=alice,
            target=acme,
            relation_type=works_at,
            graph=graph,
            defaults={"label": "employee", "metadata": {"since": int(fake.year())}},
        )
        DataEdge.objects.get_or_create(
            source=alice,
            target=bob,
            relation_type=knows,
            graph=graph,
            defaults={"label": "friend", "metadata": {"met": fake.city()}},
        )


