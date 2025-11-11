from django.core.management.base import BaseCommand
from finance.models import Account as SQLAccount, Transaction as SQLTransaction
from finance.graph.sync import AccountConversionStrategy, TransactionConversionStrategy
from toto.neo4j import is_connected as is_neo4j_connected


class Command(BaseCommand):
    help = "Synchronize SQL Accounts and Transactions into Neo4j graph database"

    def handle(self, *args, **options):

        if not is_neo4j_connected():
            self.stderr.write(self.style.ERROR("Neo4j is not connected. Aborting."))
            return

        self.stdout.write(self.style.NOTICE("Starting Finance graph synchronization..."))

        # Step 2: sync Accounts
        self.stdout.write("Syncing Accounts...")
        account_strategy = AccountConversionStrategy()
        account_strategy.execute(SQLAccount.objects.all())

        # Step 3: sync Transactions
        self.stdout.write("Syncing Transactions...")
        transaction_strategy = TransactionConversionStrategy()
        transaction_strategy.execute(SQLTransaction.objects.all())

        self.stdout.write(self.style.SUCCESS("Finance graph synchronization completed successfully."))
