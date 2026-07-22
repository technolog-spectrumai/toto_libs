from django.core.management.base import BaseCommand

from toto.ravioli.connection import Neo4jClient, is_enabled
from toto.sql_neo4j_sync.loader import load_all_configs, validate_configs
from toto.sql_neo4j_sync.models import GraphChangeEvent
from toto.sql_neo4j_sync.projection import ProjectionRunner
from toto.sql_neo4j_sync.sync import (
    mark_event_done,
    mark_event_failed,
    mark_event_processing,
    process_graph_event,
)


class Command(BaseCommand):
    help = "Drain pending ravioli graph-change events into Neo4j"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=500)
        parser.add_argument("--retry-failed", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        configs = load_all_configs()
        errors = validate_configs(configs)
        if errors:
            for err in errors:
                self.stderr.write(self.style.ERROR(f"  • {err}"))
            raise SystemExit(1)

        statuses = [GraphChangeEvent.STATUS_PENDING]
        if options["retry_failed"]:
            statuses.append(GraphChangeEvent.STATUS_FAILED)

        qs = (
            GraphChangeEvent.objects
            .filter(status__in=statuses)
            .order_by("created_at", "id")[:options["limit"]]
        )
        events = list(qs)

        if options["dry_run"]:
            for event in events:
                self.stdout.write(
                    f"{event.id}: {event.action} "
                    f"{event.graph_label} {event.object_uuid}"
                )
            self.stdout.write(f"Would process {len(events)} event(s).")
            return

        if not is_enabled():
            self.stderr.write(
                self.style.WARNING(
                    "RAVIOLI_ENABLED is False — skipping graph sync."
                )
            )
            raise SystemExit(0)

        client = Neo4jClient()
        processed = 0
        failed = 0

        try:
            runner = ProjectionRunner(client, configs)
            for event in events:
                mark_event_processing(event)
                try:
                    process_graph_event(event, runner)
                except Exception as exc:
                    mark_event_failed(event, exc)
                    failed += 1
                    self.stderr.write(
                        self.style.ERROR(f"{event.id} failed: {exc}")
                    )
                else:
                    mark_event_done(event)
                    processed += 1
        finally:
            client.close()

        self.stdout.write(
            self.style.SUCCESS(
                f"Graph outbox drained: {processed} processed, {failed} failed."
            )
        )
