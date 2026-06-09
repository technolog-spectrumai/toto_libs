"""
ravioli_rebuild — validate graph configs, then project all SQL data into Neo4j.

Steps:
  1. Load and validate all ravioli/graph/*.yaml files (same checks as
     ravioli_check — exits early if anything is wrong).
  2. Abort with a warning if RAVIOLI_ENABLED is False.
  3. Open a Neo4j connection (lazy import inside ravioli.connection).
  4. Project all nodes (one pass across every node definition).
  5. Project all links (one pass across every link definition).
"""

from django.core.management.base import BaseCommand

from toto.ravioli.connection import Neo4jClient, is_enabled
from toto.sql_neo4j_sync.loader import load_all_configs, validate_configs
from toto.sql_neo4j_sync.projection import ProjectionRunner


class Command(BaseCommand):
    help = "Rebuild the Neo4j graph from SQL data (validates config first)"

    def handle(self, *args, **options):
        # ---- Step 1: validate ----
        self.stdout.write("Loading and validating graph configs …")
        configs = load_all_configs()
        errors = validate_configs(configs)
        if errors:
            self.stderr.write(
                self.style.ERROR(f"{len(errors)} config error(s):\n")
            )
            for err in errors:
                self.stderr.write(self.style.ERROR(f"  • {err}"))
            raise SystemExit(1)
        self.stdout.write(f"  {len(configs)} config file(s) valid.")

        # ---- Step 2: check enabled ----
        if not is_enabled():
            self.stderr.write(
                self.style.WARNING(
                    "RAVIOLI_ENABLED is False — skipping Neo4j rebuild."
                )
            )
            raise SystemExit(0)

        # ---- Step 3: connect ----
        self.stdout.write("Connecting to Neo4j …")
        client = Neo4jClient()

        try:
            runner = ProjectionRunner(client, configs)

            # ---- Step 4: nodes ----
            self.stdout.write("Projecting nodes …")
            runner.project_all_nodes()
            self.stdout.write("  Nodes done.")

            # ---- Step 5: links ----
            self.stdout.write("Projecting links …")
            runner.project_all_links()
            self.stdout.write("  Links done.")

        finally:
            client.close()

        self.stdout.write(self.style.SUCCESS("Graph rebuild complete."))
