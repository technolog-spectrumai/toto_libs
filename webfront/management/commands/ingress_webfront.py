import os
from django.conf import settings
from rest_framework.reverse import reverse_lazy
from oya.ingress import IngressCommand

from webfront.models import (
    CypherQuery,
    DynamicPage,
    FileWorkflow,
    GraphWorkflow,
)

from mandragora.models import LambdaNode, LambdaLayer
from vault.models import Bucket


def load_lambda(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Lambda file not found: {path}")
    with open(path, "r") as f:
        return f.read()


class Command(IngressCommand):
    help = "Seeds core Cypher queries, DynamicPages, GraphWorkflows, and FileWorkflows."

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
        # Create Lambda Layers
        # ---------------------------------------------------------

        graph_layer, _ = LambdaLayer.objects.get_or_create(
            name="GraphLayer",
            defaults={
                "use_numpy": True,
                "use_networkx": True,
            }
        )

        file_layer, _ = LambdaLayer.objects.get_or_create(
            name="FileLayer",
            defaults={
                "use_numpy": True,
            }
        )

        # ---------------------------------------------------------
        # 1. CypherQuery: All Nodes and Edges
        # ---------------------------------------------------------

        cypher_query = CypherQuery.objects.update_or_create(
            name="All Nodes and Edges",
            defaults={
                "description": "Fetch all nodes and relationships (limit 500).",
                "query": "MATCH (n)-[r]->(m) RETURN n,r,m LIMIT 500",
            },
        )[0]

        # ---------------------------------------------------------
        # 2. GraphWorkflow: Graph → Lambda
        # ---------------------------------------------------------

        cypher_lambda = LambdaNode.objects.update_or_create(
            name="CypherQueryProcessor",
            defaults={
                "layer": graph_layer,
                "code": load_lambda(os.path.join(base_path, "cypher.py")),
            },
        )[0]

        GraphWorkflow.objects.update_or_create(
            name="All Nodes Workflow",
            defaults={
                "slug": "all-nodes",
                "cypher_query": cypher_query,
                "lambda_node": cypher_lambda,
                "description": "Runs a Cypher query and transforms the graph.",
                "is_active": True,
            },
        )

        # ---------------------------------------------------------
        # 3. DynamicPage: Kanban
        # ---------------------------------------------------------

        kanban_lambda = LambdaNode.objects.update_or_create(
            name="KanbanPageRenderer",
            defaults={
                "layer": graph_layer,
                "code": load_lambda(os.path.join(base_path, "kanban.py")),
            },
        )[0]

        DynamicPage.objects.update_or_create(
            name="Story Points Kanban",
            defaults={
                "slug": "kanban",
                "lambda_node": kanban_lambda,
                "is_active": True,
            },
        )

        # ---------------------------------------------------------
        # 4. FileWorkflow: File Processor
        # ---------------------------------------------------------

        bucket = Bucket.objects.first()

        file_lambda = LambdaNode.objects.update_or_create(
            name="FileProcessorLambda",
            defaults={
                "layer": file_layer,
                "code": load_lambda(os.path.join(base_path, "json.py")),
            },
        )[0]

        FileWorkflow.objects.update_or_create(
            name="File Processor",
            defaults={
                "description": "Processes uploaded files and returns JSON output.",
                "bucket": bucket,
                "lambda_node": file_lambda,
                "is_active": True,
            },
        )

        calendar_layer, _ = LambdaLayer.objects.get_or_create(
            name="GraphLayer",
            defaults={
                "use_numpy": True,
                "use_networkx": True,
                "use_random": True
            }
        )

        calendar_lambda = LambdaNode.objects.update_or_create(
            name="CalendarRenderer",
            defaults={
                "layer": calendar_layer,
                "code": load_lambda(os.path.join(base_path, "calendar.py")),
            },
        )[0]
