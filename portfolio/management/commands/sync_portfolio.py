from django.core.management.base import BaseCommand
from portfolio.models import Company as SQLCompany, Shareholder as SQLShareholder, FundingRound as SQLFundingRound
from portfolio.graph.sync import CompanyConversionStrategy, ShareholderConversionStrategy, FundingRoundConversionStrategy
from toto.neo4j import is_connected as is_neo4j_connected


class Command(BaseCommand):
    help = "Synchronize SQL Companies, Shareholders, and FundingRounds into Neo4j graph database"

    def handle(self, *args, **options):

        if not is_neo4j_connected():
            self.stderr.write(self.style.ERROR("Neo4j is not connected. Aborting."))
            return

        self.stdout.write(self.style.NOTICE("Starting portfolio graph synchronization..."))

        # --- Sync Companies ---
        self.stdout.write("Syncing Companies...")
        company_strategy = CompanyConversionStrategy()
        company_strategy.execute(SQLCompany.objects.all())

        # --- Sync Shareholders ---
        self.stdout.write("Syncing Shareholders...")
        shareholder_strategy = ShareholderConversionStrategy()
        shareholder_strategy.execute(SQLShareholder.objects.select_related("company", "social_entity").all())

        # --- Sync Funding Rounds ---
        self.stdout.write("Syncing Funding Rounds...")
        funding_strategy = FundingRoundConversionStrategy()
        funding_strategy.execute(SQLFundingRound.objects.select_related("venture", "currency", "transaction").all())

        self.stdout.write(self.style.SUCCESS("✅ Portfolio graph synchronization completed successfully."))
