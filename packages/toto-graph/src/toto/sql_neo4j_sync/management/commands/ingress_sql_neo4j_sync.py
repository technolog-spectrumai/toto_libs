from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Seed SQL→Neo4j sync workflows (generate/apply/clear) and a sample NeoJSON file"

    def process(self):
        self._ensure_sync_workflows()
        self._ensure_sample_neojson_file()

    def _ensure_sync_workflows(self):
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
                task_name="sql_neo4j_sync_generate_plan",
                position_x=0,
                position_y=0,
            )
            apply = WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Apply projection plan",
                task_name="sql_neo4j_sync_apply_plan",
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
                task_name="sql_neo4j_sync_generate_plan",
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
                task_name="sql_neo4j_sync_apply_plan",
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
                task_name="sql_neo4j_sync_clear_db",
                position_x=0,
                position_y=0,
            )
            self.stdout.write(self.style.SUCCESS("📊 Created workflow: Ravioli Clear DB"))
        else:
            self.stdout.write(self.style.WARNING("ℹ️  Workflow 'Ravioli Clear DB' already exists"))

    def _ensure_sample_neojson_file(self):
        """Seed a demo .neojson VaultFile so the NeoJSON editor has content to open."""
        from django.contrib.auth.models import User
        from django.core.files.base import ContentFile

        from toto.ravioli import neojson
        from toto.vault.models import Bucket, VaultFile

        owner = User.objects.filter(is_superuser=True).order_by("pk").first()
        if owner is None:
            self.stdout.write(self.style.WARNING("ℹ️  No superuser found — skipping NeoJSON sample seed."))
            return

        bucket = (
            Bucket.objects.filter(slug="general").first()
            or Bucket.objects.filter(slug="media").first()
            or Bucket.objects.filter(owner=owner).first()
        )
        if bucket is None:
            self.stdout.write(self.style.WARNING("ℹ️  No bucket found — run ingress_vault first; skipping NeoJSON sample."))
            return

        key = "sample-graph-neojson"
        if VaultFile.objects.filter(bucket=bucket, key=key).exists():
            self.stdout.write(self.style.WARNING(f"ℹ️  Sample .neojson file already exists in bucket '{bucket.slug}'."))
            return

        graph = neojson.from_ravioli(
            [
                {"id": "n1", "labels": ["Person"], "props": {"name": "Alice", "role": "Architect"}},
                {"id": "n2", "labels": ["Person"], "props": {"name": "Bob", "role": "Engineer"}},
            ],
            [
                {"id": "rel-1", "type": "KNOWS", "start": "n1", "end": "n2", "props": {"since": 2021}},
            ],
            metadata={"source": "ingress:sql_neo4j_sync/sample"},
        )
        content = neojson.dumps(graph).encode("utf-8")

        vf = VaultFile(
            owner=owner,
            title="sample-graph.neojson",
            bucket=bucket,
            file_type="neojson",
            is_public=True,
            key=key,
        )
        vf.file.save("sample-graph.neojson", ContentFile(content), save=False)
        vf.file_size_bytes = len(content)
        vf.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"✅ Created sample .neojson file (pk={vf.pk}) in bucket '{bucket.slug}'."
            )
        )
