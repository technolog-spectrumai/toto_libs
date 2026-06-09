from django.core.management.base import BaseCommand

from toto.ravioli.connection import Neo4jClient, is_enabled
from toto.sql_neo4j_sync.loader import load_all_configs, validate_configs
from toto.sql_neo4j_sync.projection import ProjectionRunner, link_key


class Command(BaseCommand):
    help = "Reconcile ravioli-owned Neo4j nodes with SQL rows"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--resync-links",
            action="store_true",
            help="Also rebuild every configured relationship definition.",
        )

    def handle(self, *args, **options):
        configs = load_all_configs()
        errors = validate_configs(configs)
        if errors:
            for err in errors:
                self.stderr.write(self.style.ERROR(f"  • {err}"))
            raise SystemExit(1)

        if not is_enabled():
            self.stderr.write(
                self.style.WARNING(
                    "RAVIOLI_ENABLED is False — skipping reconciliation."
                )
            )
            raise SystemExit(0)

        client = Neo4jClient()
        dry_run = options["dry_run"]
        total_missing = 0
        total_stale = 0

        try:
            runner = ProjectionRunner(client, configs)

            for node_def in runner.node_defs():
                label = node_def["label"]
                info = runner._label_map[label]
                uuid_field = info["uuid_field"]
                model = info["model"]

                sql_uuids = set(
                    str(value)
                    for value in model.objects.values_list(uuid_field, flat=True)
                )
                records = client.run_cypher(
                    f"MATCH (n:{label}) WHERE n.uuid IS NOT NULL RETURN n.uuid AS uuid"
                )
                graph_uuids = {
                    str(record["uuid"])
                    for record in records
                    if record["uuid"] is not None
                }

                missing = sorted(sql_uuids - graph_uuids)
                stale = sorted(graph_uuids - sql_uuids)
                total_missing += len(missing)
                total_stale += len(stale)

                self.stdout.write(
                    f"{label}: {len(missing)} missing, {len(stale)} stale"
                )

                if dry_run:
                    continue

                for uuid in missing:
                    runner.project_node_by_label(label, uuid)
                    runner.project_outgoing_links_by_label(label, uuid)
                for uuid in stale:
                    runner.delete_node_by_label(label, uuid)

            if options["resync_links"]:
                self.stdout.write("Resyncing configured relationships …")
                if not dry_run:
                    for link_def in runner.link_defs():
                        runner.project_link_by_key(link_key(link_def))
        finally:
            client.close()

        prefix = "Would reconcile" if dry_run else "Reconciled"
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}: {total_missing} missing node(s), "
                f"{total_stale} stale node(s)."
            )
        )
