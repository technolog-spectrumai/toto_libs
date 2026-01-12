import os
from django.conf import settings
from rest_framework.reverse import reverse_lazy
from oya.ingress import IngressCommand
from webfront.models import (
    CypherQuery,
    DynamicPage,
    FileWorkflow,
    GraphWorkflow,
    Widget
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
            }
        )[0]

        # ---------------------------------------------------------
        # 2. GraphWorkflow: Graph → Lambda
        # ---------------------------------------------------------

        cypher_lambda = LambdaNode.objects.update_or_create(
            name="CypherQueryProcessor",
            defaults={
                "layer": graph_layer,
                "code": load_lambda(os.path.join(base_path, "cypher.py")),
            }
        )[0]

        GraphWorkflow.objects.update_or_create(
            name="All Nodes Workflow",
            defaults={
                "slug": "all-nodes",
                "cypher_query": cypher_query,
                "lambda_node": cypher_lambda,
                "description": "Runs a Cypher query and transforms the graph.",
                "is_active": True,
            }
        )

        # ---------------------------------------------------------
        # 3. DynamicPage: Kanban
        # ---------------------------------------------------------

        # ---------------------------------------------------------
        # 3. DynamicPage: Story Points Dashboard (ETL + Widgets)
        # ---------------------------------------------------------

        # 3a. Page-level ETL lambda
        page_lambda = LambdaNode.objects.update_or_create(
            name="StoryPointsExtractor",
            defaults={
                "layer": graph_layer,
                "code": load_lambda(os.path.join(base_path, "kanban.py")),
            }
        )[0]

        page, _ = DynamicPage.objects.update_or_create(
            name="Story Points Dashboard",
            defaults={
                "slug": "story-points",
                "lambda_node": page_lambda,
                "is_active": True,
                "cypher_query": cypher_query
            }
        )

        # ---------------------------------------------------------
        # 3b. Widget-level lambdas
        # ---------------------------------------------------------

        table_lambda = LambdaNode.objects.update_or_create(
            name="StoryPointsTableWidget",
            defaults={
                "layer": graph_layer,
                "code": load_lambda(os.path.join(base_path, "table.py")),
            }
        )[0]

        bar_chart_lambda = LambdaNode.objects.update_or_create(
            name="StoryPointsBarChartWidget",
            defaults={
                "layer": graph_layer,
                "code": load_lambda(os.path.join(base_path, "bars.py")),
            }
        )[0]
        form_lambda = LambdaNode.objects.update_or_create(
            name="UserFormWidget",
            defaults={
                "layer": graph_layer,
                "code": load_lambda(os.path.join(base_path, "form.py")),
            }
        )[0]

        # ---------------------------------------------------------
        # 3c. Create widgets in DB
        # ---------------------------------------------------------

        Widget.objects.update_or_create(
            page=page,
            order=1,
            defaults={
                "title": "Story Points Table",
                "type": "table",
                "lambda_node": table_lambda,
            }
        )

        Widget.objects.update_or_create(
            page=page,
            order=2,
            defaults={
                "title": "Completed vs In‑Progress",
                "type": "chart",
                "lambda_node": bar_chart_lambda,
            }
        )
        Widget.objects.update_or_create(
            page=page,
            order=3,
            defaults={
                "title": "User Form",
                "type": "form",
                "lambda_node": form_lambda,
            }
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
            }
        )[0]

        FileWorkflow.objects.update_or_create(
            name="File Processor",
            defaults={
                "description": "Processes uploaded files and returns JSON output.",
                "bucket": bucket,
                "lambda_node": file_lambda,
                "is_active": True,
            }
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
            }
        )[0]

        # map_lambda = LambdaNode.objects.update_or_create(
        #     name="MapRenderer",
        #     defaults={
        #         "layer": calendar_layer,
        #         "code": load_lambda(os.path.join(base_path, "map.py")),
        #     },
        # )[0]
