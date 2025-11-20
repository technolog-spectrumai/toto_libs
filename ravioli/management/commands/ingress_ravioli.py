from django.conf import settings
from faker import Faker
from oya.ingress import IngressCommand
from rest_framework.reverse import reverse_lazy

from ravioli.models import CypherQuery  # adjust import path to where your model lives

fake = Faker()


class Command(IngressCommand):
    help = "Creates a demo Bolt user with read-only permissions and a default Cypher query"

    def process(self, _):
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

        # Create or update the query in DB
        CypherQuery.objects.update_or_create(
            name="All Nodes and Edges",
            defaults={
                "description": "Fetch all nodes and relationships in the graph (limited to 500).",
                "query": cypher.strip(),
                "is_active": True,
            },
        )
