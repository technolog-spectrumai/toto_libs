from django.core.management.base import BaseCommand
from notes.models import Note as SQLNote, Tag as SQLTag
from notes.graph.sync import NoteConversionStrategy, TagConversionStrategy
from toto.neo4j import is_connected as is_neo4j_connected


class Command(BaseCommand):
    help = "Synchronize SQL Notes and Tags into Neo4j graph database"

    def handle(self, *args, **options):
        if not is_neo4j_connected():
            self.stderr.write(self.style.ERROR("❌ Neo4j is not connected. Aborting."))
            return

        self.stdout.write(self.style.NOTICE("Starting Notes graph synchronization..."))

        # Step 1: sync Tags
        self.stdout.write("Syncing Tags...")
        tag_strategy = TagConversionStrategy()
        tag_strategy.execute(SQLTag.objects.all())

        # Step 2: sync Notes
        self.stdout.write("Syncing Notes...")
        note_strategy = NoteConversionStrategy()
        note_strategy.execute(SQLNote.objects.all())

        self.stdout.write(self.style.SUCCESS("✅ Notes graph synchronization completed successfully."))
