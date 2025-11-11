# myapp/management/commands/clear_neo4j.py
from django.core.management.base import BaseCommand
from toto.neo4j import is_connected as is_neo4j_connected
from neomodel import db


class Command(BaseCommand):
    help = "Clear all nodes and relationships from the Neo4j database"

    def handle(self, *args, **options):

        if not is_neo4j_connected():
            self.stderr.write(self.style.ERROR("Neo4j is not connected. Aborting."))
            return

        self.stdout.write(self.style.NOTICE("Clearing Neo4j database..."))

        # Wipe all nodes + relationships
        db.cypher_query("MATCH (n) DETACH DELETE n")

        self.stdout.write(self.style.SUCCESS("Neo4j database cleared successfully."))
