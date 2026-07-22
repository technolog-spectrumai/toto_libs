from toto.ingress import IngressCommand
from toto.ravioli.models import CypherQuery, CypherQueryResult


class Command(IngressCommand):
    help = "Seed Ravioli with default Knowledge Graph and predefined workflows"

    def process(self):
        self._ensure_workflows()
        self._ensure_graph_analysis_workflow()
        self._ensure_cypher_queries()

        # Rich ingress seeds a small demo graph into Neo4j so the default
        # "Get Entire Graph" query returns something; thin ingress skips all
        # Neo4j round-trips for a fast bring-up.
        if self.rich:
            self._seed_sample_graph()
        else:
            self.stdout.write(self.style.WARNING(
                "⏩ Thin ingress (RAVIOLI_RICH_INGRESS=0) — skipping Neo4j sample graph."
            ))

    def _seed_sample_graph(self):
        from toto.ravioli.connection import Neo4jClient, is_enabled

        if not is_enabled():
            self.stdout.write(self.style.WARNING(
                "ℹ️ Neo4j disabled (RAVIOLI_ENABLED=0) — skipping sample graph."
            ))
            return

        client = Neo4jClient()
        try:
            existing = client.run_cypher("MATCH (n:Concept) RETURN count(n) AS c")
            if existing and existing[0]["c"]:
                self.stdout.write(self.style.WARNING(
                    f"ℹ️ Sample graph already present ({existing[0]['c']} Concept node(s)) — skipping."
                ))
                return

            concepts = [
                ("memory", "compression"), ("compression", "naming"),
                ("naming", "caching"), ("caching", "latency"),
                ("latency", "feedback"), ("feedback", "memory"),
                ("trust", "incentives"),
            ]
            names = {n for pair in concepts for n in pair}
            for name in sorted(names):
                client.run_cypher(
                    "MERGE (n:Concept {uuid: $uuid}) SET n.name = $name",
                    {"uuid": f"concept:{name}", "name": name},
                )
            for a, b in concepts:
                client.run_cypher(
                    "MATCH (a:Concept {uuid: $a}) MATCH (b:Concept {uuid: $b}) "
                    "MERGE (a)-[:RELATES_TO]->(b)",
                    {"a": f"concept:{a}", "b": f"concept:{b}"},
                )
            self.stdout.write(self.style.SUCCESS(
                f"💡 Seeded sample graph: {len(names)} Concept node(s), {len(concepts)} edge(s)."
            ))
        except Exception as exc:  # noqa: BLE001 — Neo4j unreachable: don't fail ingress
            self.stdout.write(self.style.WARNING(
                f"ℹ️ Neo4j unreachable — skipping sample graph: {exc}"
            ))
        finally:
            client.close()

    def _ensure_workflows(self):
        from toto.workflows.models import Workflow, WorkflowEdge, WorkflowNode

        # --- Ravioli Graph Search: single node ---
        wf6, created6 = Workflow.objects.get_or_create(
            slug="ravioli-graph-search",
            defaults={
                "name": "Ravioli Graph Search",
                "description": (
                    "Run a graph search (basic / advanced / deep) against Neo4j "
                    "and return matching nodes with their properties."
                ),
            },
        )
        if created6:
            WorkflowNode.objects.create(
                workflow=wf6,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Search graph nodes",
                task_name="ravioli_graph_search",
                position_x=0,
                position_y=0,
            )
            self.stdout.write(self.style.SUCCESS("📊 Created workflow: Ravioli Graph Search"))
        else:
            self.stdout.write(self.style.WARNING("ℹ️  Workflow 'Ravioli Graph Search' already exists"))

        # --- Ravioli GraphRAG Describe: single node (Steven answers over the graph) ---
        wf_gr, created_gr = Workflow.objects.get_or_create(
            slug="ravioli-graphrag-describe",
            defaults={
                "name": "Ravioli GraphRAG Describe",
                "description": (
                    "Ask AI to describe / answer questions about the graph via "
                    "read-only GraphRAG (vector + Text2Cypher over Neo4j)."
                ),
            },
        )
        if created_gr:
            WorkflowNode.objects.create(
                workflow=wf_gr,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="GraphRAG describe",
                task_name="ravioli_graphrag_describe",
                position_x=0,
                position_y=0,
            )
            self.stdout.write(self.style.SUCCESS("🤖 Created workflow: Ravioli GraphRAG Describe"))
        else:
            self.stdout.write(self.style.WARNING("ℹ️  Workflow 'Ravioli GraphRAG Describe' already exists"))

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
