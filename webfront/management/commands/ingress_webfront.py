import os
from django.conf import settings
from rest_framework.reverse import reverse_lazy

from oya.ingress import IngressCommand
from webfront.models import CypherQuery, DynamicPage


def load_lambda(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Lambda file not found: {path}")
    with open(path, "r") as f:
        return f.read()


class Command(IngressCommand):
    help = "Seeds only the core Cypher query and the Centrality Analysis page."

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

        base_path = os.path.join(settings.BASE_DIR, "..", "data", "webfront")

        # ---------------------------------------------------------
        # 1. CypherQuery: All Nodes and Edges
        # ---------------------------------------------------------
        CypherQuery.objects.update_or_create(
            name="All Nodes and Edges",
            defaults={
                "description": "Fetch all nodes and relationships (limit 500).",
                "query": "MATCH (n)-[r]->(m) RETURN n,r,m LIMIT 500",
                "code": load_lambda(
                    os.path.join(base_path, "cypher.py")
                ),
                "is_active": True,
            },
        )

        DynamicPage.objects.update_or_create(
            name="Story Points Kanban",
            defaults={
                "slug": "kanban",
                "query": "MATCH (n)-[r]->(m) RETURN n,r,m LIMIT 500",
                "code": load_lambda(
                    os.path.join(base_path, "kanban.py")
                ),
            },
        )
