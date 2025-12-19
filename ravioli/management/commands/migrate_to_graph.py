from django.core.management.base import BaseCommand
from ravioli.models import Graph
from ravioli.conversion import GraphConverter


class Command(BaseCommand):
    help = "Migrates Django models into the Graph/Node/Edge structure."

    def add_arguments(self, parser):
        parser.add_argument(
            "graph_name",
            type=str,
            help="Name of the Graph object to migrate data into."
        )

    def handle(self, *args, **options):
        graph_name = options["graph_name"]

        try:
            graph, _ = Graph.objects.get_or_create(name=graph_name)

            migrator = GraphConverter(graph=graph)
            migrator.migrate()

            self.stdout.write(
                self.style.SUCCESS(
                    f"Graph migration completed successfully into graph '{graph_name}'"
                )
            )

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Migration failed: {e}"))

