from faker import Faker
from oya.ingress import IngressCommand
from rest_framework.reverse import reverse_lazy
from webfront.models import CypherQuery


fake = Faker()


class Command(IngressCommand):
    help = "Seeds a demo graph with sample nodes, relations, and a Cypher query"

    def process(self):
        # Dashboard block
        self.create_dashboard_item(
            title="Graph Database",
            icon="fa-solid fa-database",
            description="Demo graph with sample data",
            link=reverse_lazy("webfront:graph"),
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