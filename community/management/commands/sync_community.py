from django.core.management.base import BaseCommand
from community.models import Community as SQLCommunity, CommunityMember as SQLCommunityMember
from community.graph.sync import CommunityConversionStrategy, CommunityMemberConversionStrategy
from toto.neo4j import is_connected as is_neo4j_connected


class Command(BaseCommand):
    help = "Synchronize SQL Community and CommunityMember models into Neo4j graph database"

    def handle(self, *args, **options):

        if not is_neo4j_connected():
            self.stderr.write(self.style.ERROR("Neo4j is not connected. Aborting."))
            return

        self.stdout.write(self.style.NOTICE("Starting Community graph synchronization..."))

        # Sync Communities
        community_strategy = CommunityConversionStrategy()
        community_strategy.execute(SQLCommunity.objects.all())

        # Sync Members
        member_strategy = CommunityMemberConversionStrategy()
        member_strategy.execute(SQLCommunityMember.objects.select_related("user").all())

        self.stdout.write(self.style.SUCCESS("Community graph synchronization completed successfully."))
