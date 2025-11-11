from django.core.management.base import BaseCommand
from events.models import Event as SQLEvent
from events.graph.sync import EventConversionStrategy
from toto.neo4j import is_connected as is_neo4j_connected


class Command(BaseCommand):
    help = "Synchronize SQL Events into Neo4j graph database"

    def handle(self, *args, **options):

        if not is_neo4j_connected():
            self.stderr.write(self.style.ERROR("❌ Neo4j is not connected. Aborting."))
            return

        self.stdout.write(self.style.NOTICE("Starting Events graph synchronization..."))

        # Step 1: sync Events
        self.stdout.write("Syncing Events...")
        event_strategy = EventConversionStrategy()
        event_strategy.execute(SQLEvent.objects.all())

        self.stdout.write(self.style.SUCCESS("✅ Events graph synchronization completed successfully."))
