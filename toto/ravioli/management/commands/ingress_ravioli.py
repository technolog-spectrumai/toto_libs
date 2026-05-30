from toto.ingress import IngressCommand
from toto.ravioli.models import CypherQuery, CypherQueryResult


class Command(IngressCommand):
    help = "Seed Ravioli with default Knowledge Graph and predefined workflows"

    def process(self):
        self._check_tariff()
        self._ensure_workflows()
        self._ensure_graph_analysis_workflow()
        self._ensure_cypher_queries()

    def _ensure_workflows(self):
        from toto.workflows.models import Workflow, WorkflowEdge, WorkflowNode

        # --- Ravioli Sync: generate plan → apply plan ---
        wf, created = Workflow.objects.get_or_create(
            slug="ravioli-sync",
            defaults={
                "name": "Ravioli Sync",
                "description": (
                    "Generate a graph projection plan for all labels "
                    "and immediately apply it to Neo4j."
                ),
            },
        )
        if created:
            generate = WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Generate projection plan",
                task_name="ravioli_generate_plan",
                position_x=0,
                position_y=0,
            )
            apply = WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Apply projection plan",
                task_name="ravioli_apply_plan",
                position_x=300,
                position_y=0,
            )
            WorkflowEdge.objects.create(workflow=wf, source=generate, target=apply)
            self.stdout.write(self.style.SUCCESS("📊 Created workflow: Ravioli Sync"))
        else:
            self.stdout.write(self.style.WARNING("ℹ️  Workflow 'Ravioli Sync' already exists"))

        # --- Ravioli Generate Plan: single node ---
        wf3, created3 = Workflow.objects.get_or_create(
            slug="ravioli-generate-plan",
            defaults={
                "name": "Ravioli Generate Plan",
                "description": "Generate a graph projection plan for selected labels.",
            },
        )
        if created3:
            WorkflowNode.objects.create(
                workflow=wf3,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Generate projection plan",
                task_name="ravioli_generate_plan",
                position_x=0,
                position_y=0,
            )
            self.stdout.write(self.style.SUCCESS("📊 Created workflow: Ravioli Generate Plan"))
        else:
            self.stdout.write(self.style.WARNING("ℹ️  Workflow 'Ravioli Generate Plan' already exists"))

        # --- Ravioli Apply Plan: single node ---
        wf4, created4 = Workflow.objects.get_or_create(
            slug="ravioli-apply-plan",
            defaults={
                "name": "Ravioli Apply Plan",
                "description": "Apply an existing ready projection plan to Neo4j.",
            },
        )
        if created4:
            WorkflowNode.objects.create(
                workflow=wf4,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Apply projection plan",
                task_name="ravioli_apply_plan",
                position_x=0,
                position_y=0,
            )
            self.stdout.write(self.style.SUCCESS("📊 Created workflow: Ravioli Apply Plan"))
        else:
            self.stdout.write(self.style.WARNING("ℹ️  Workflow 'Ravioli Apply Plan' already exists"))

        # --- Ravioli Clear DB: single destructive node ---
        wf2, created2 = Workflow.objects.get_or_create(
            slug="ravioli-clear-db",
            defaults={
                "name": "Ravioli Clear DB",
                "description": (
                    "Delete all ravioli-owned nodes and relationships from Neo4j."
                ),
            },
        )
        if created2:
            WorkflowNode.objects.create(
                workflow=wf2,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Clear ravioli-owned data",
                task_name="ravioli_clear_db",
                position_x=0,
                position_y=0,
            )
            self.stdout.write(self.style.SUCCESS("📊 Created workflow: Ravioli Clear DB"))
        else:
            self.stdout.write(self.style.WARNING("ℹ️  Workflow 'Ravioli Clear DB' already exists"))

        # --- Ravioli Run Cypher Query: single node ---
        wf5, created5 = Workflow.objects.get_or_create(
            slug="ravioli-run-cypher-query",
            defaults={
                "name": "Ravioli Run Cypher Query",
                "description": "Run a saved Cypher query against Neo4j and cache its results.",
            },
        )
        if created5:
            WorkflowNode.objects.create(
                workflow=wf5,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Run Cypher query",
                task_name="ravioli_run_cypher_query",
                position_x=0,
                position_y=0,
            )
            self.stdout.write(self.style.SUCCESS("📊 Created workflow: Ravioli Run Cypher Query"))
        else:
            self.stdout.write(self.style.WARNING("ℹ️  Workflow 'Ravioli Run Cypher Query' already exists"))

    def _ensure_graph_analysis_workflow(self):
        from toto.workflows.models import LambdaFunction, Workflow, WorkflowEdge, WorkflowNode

        _BASIC_LAMBDA_CONTENT = (
            "import json\n"
            "\n"
            "data = _input.get('data', {})\n"
            "graph_payload = data.get('graph') or {}\n"
            "\n"
            "try:\n"
            "    import networkx as nx\n"
            "    G = nx.node_link_graph(graph_payload)\n"
            "    metrics = {\n"
            "        'node_count': G.number_of_nodes(),\n"
            "        'edge_count': G.number_of_edges(),\n"
            "        'density': round(nx.density(G), 6),\n"
            "    }\n"
            "    if G.number_of_nodes() > 0 and G.number_of_edges() > 0:\n"
            "        try:\n"
            "            metrics['is_weakly_connected'] = nx.is_weakly_connected(G)\n"
            "        except Exception:\n"
            "            pass\n"
            "except ImportError:\n"
            "    metrics = data.get('graph_summary') or {}\n"
            "\n"
            "out = {k: v for k, v in data.items() if k != 'graph'}\n"
            "out['metrics'] = metrics\n"
            "print(json.dumps({'data': out}))\n"
        )

        lambda_fn, lf_created = LambdaFunction.objects.get_or_create(
            function_name="graph_analysis_basic_metrics",
            defaults={"content": _BASIC_LAMBDA_CONTENT},
        )
        if lf_created:
            self.stdout.write(self.style.SUCCESS("🔧 Created LambdaFunction: graph_analysis_basic_metrics"))
        else:
            self.stdout.write(self.style.WARNING("ℹ️  LambdaFunction 'graph_analysis_basic_metrics' already exists"))

        wf, created = Workflow.objects.get_or_create(
            slug="graph-analysis-basic",
            defaults={
                "name": "Graph Analysis Basic",
                "description": (
                    "Prepare a NetworkX graph from a Cypher query result, "
                    "compute basic metrics, and save the output to Vault."
                ),
            },
        )
        if created:
            prepare = WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Prepare graph",
                task_name="ravioli_prepare_graph_analysis",
                position_x=0,
                position_y=0,
            )
            compute = WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.LAMBDA,
                label="Compute metrics",
                lambda_function=lambda_fn,
                position_x=300,
                position_y=0,
            )
            save = WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Save to Vault",
                task_name="ravioli_save_graph_analysis_output",
                position_x=600,
                position_y=0,
            )
            WorkflowEdge.objects.create(workflow=wf, source=prepare, target=compute)
            WorkflowEdge.objects.create(workflow=wf, source=compute, target=save)
            self.stdout.write(self.style.SUCCESS("📊 Created workflow: Graph Analysis Basic"))
        else:
            self.stdout.write(self.style.WARNING("ℹ️  Workflow 'Graph Analysis Basic' already exists"))

    def _ensure_cypher_queries(self):
        query_text = """
        MATCH (n)-[r]-(m)
        RETURN n, r, m
        LIMIT 500
        """

        q, created = CypherQuery.objects.get_or_create(
            name="Get Entire Graph",
            defaults={
                "query": query_text.strip(),
                "description": "Returns a large portion of the graph: nodes + relationships.",
            },
        )

        if created:
            self.stdout.write(self.style.SUCCESS("📝 Created Cypher query: Get Entire Graph"))
        else:
            self.stdout.write(self.style.WARNING("ℹ️  Cypher query already exists"))

        CypherQueryResult.objects.get_or_create(query=q)
        self.stdout.write(self.style.SUCCESS("📊 Created query result entry"))

    def _check_tariff(self):
        from django.apps import apps as django_apps
        if not django_apps.is_installed("toto.tariffs"):
            self.stdout.write("  toto.tariffs not installed — skipping tariff check.")
            return
        from toto.tariffs.models import Tariff
        if Tariff.objects.filter(code="NEO4J-GRAPH").exists():
            self.stdout.write("  [ravioli] NEO4J-GRAPH tariff: ready.")
        else:
            self.stdout.write(self.style.WARNING(
                "  [ravioli] NEO4J-GRAPH tariff not found — run ingress_tariffs first."
            ))
