from django.core.management.base import BaseCommand
from federal.models import Federation as SQLFederation, FederatedIdentity as SQLIdentity
from federal.sync import FederationConversionStrategy, IdentityConversionStrategy
from toto.neo4j import is_connected as is_neo4j_connected


class Command(BaseCommand):
    help = "Synchronize SQL Federations and FederatedIdentities into Neo4j graph database"

    def handle(self, *args, **options):

        if not is_neo4j_connected():
            self.stderr.write(self.style.ERROR("Neo4j is not connected. Aborting."))
            return

        self.stdout.write(self.style.NOTICE("Starting graph synchronization..."))

        # --- Sync Federations ---
        self.stdout.write("Syncing Federations...")
        federation_strategy = FederationConversionStrategy()
        federation_strategy.execute(SQLFederation.objects.all())

        # --- Sync Identities ---
        self.stdout.write("Syncing Federated Identities...")
        identity_strategy = IdentityConversionStrategy()
        identity_strategy.execute(SQLIdentity.objects.select_related("federation").all())

        self.stdout.write(self.style.SUCCESS("Graph synchronization completed successfully."))
