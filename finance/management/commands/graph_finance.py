from django.core.management.base import BaseCommand
from ravioli.models import CollectionType, RelationType, AppCollector, Graph


class Command(BaseCommand):
    help = "Create graph schema and AppCollector + Graph for the finance app"

    def create_collection_types(self):
        """Define all CollectionTypes for finance models."""
        CollectionType.objects.get_or_create(name="Currency")
        CollectionType.objects.get_or_create(name="ExchangeRate")
        CollectionType.objects.get_or_create(name="Account")
        CollectionType.objects.get_or_create(name="Transaction")
        CollectionType.objects.get_or_create(name="User")
        CollectionType.objects.get_or_create(name="CommunityMember")

    def create_relation_types(self):
        """Define all RelationTypes for finance models."""
        RelationType.objects.get_or_create(
            name="Base-Currency",
            defaults={"metadata": {"from": "ExchangeRate", "to": "Currency"}}
        )
        RelationType.objects.get_or_create(
            name="Quote-Currency",
            defaults={"metadata": {"from": "ExchangeRate", "to": "Currency"}}
        )
        RelationType.objects.get_or_create(
            name="Account-Owner",
            defaults={"metadata": {"from": "Account", "to": "CommunityMember"}}
        )
        RelationType.objects.get_or_create(
            name="Account-Manager",
            defaults={"metadata": {"from": "Account", "to": "User"}}
        )
        RelationType.objects.get_or_create(
            name="Account-Currency",
            defaults={"metadata": {"from": "Account", "to": "Currency"}}
        )
        RelationType.objects.get_or_create(
            name="Transaction-Currency",
            defaults={"metadata": {"from": "Transaction", "to": "Currency"}}
        )
        RelationType.objects.get_or_create(
            name="Transaction-Source",
            defaults={"metadata": {"from": "Transaction", "to": "Account"}}
        )
        RelationType.objects.get_or_create(
            name="Transaction-Destination",
            defaults={"metadata": {"from": "Transaction", "to": "Account"}}
        )

    def create_collector_config(self):
        """Register AppCollector + Graph with descriptive config."""
        finance_config = {
            "models": {
                "Currency": {
                    "collection_type": "Currency",
                    "fields": ["symbol", "name", "is_crypto", "decimals", "active"],
                    "relations": {}
                },
                "ExchangeRate": {
                    "collection_type": "ExchangeRate",
                    "fields": ["rate", "timestamp"],
                    "relations": {
                        "base_currency": "Base-Currency",
                        "quote_currency": "Quote-Currency"
                    }
                },
                "Account": {
                    "collection_type": "Account",
                    "fields": ["name", "balance", "created_at", "active"],
                    "relations": {
                        "owner": "Account-Owner",
                        "manager": "Account-Manager",
                        "currency": "Account-Currency"
                    }
                },
                "Transaction": {
                    "collection_type": "Transaction",
                    "fields": ["name", "amount", "timestamp"],
                    "relations": {
                        "currency": "Transaction-Currency",
                        "source": "Transaction-Source",
                        "destination": "Transaction-Destination"
                    }
                },
                "User": {
                    "collection_type": "User",
                    "fields": ["username", "email"],
                    "relations": {}
                },
                "CommunityMember": {
                    "collection_type": "CommunityMember",
                    "fields": ["name"],
                    "relations": {}
                }
            }
        }

        collector, _ = AppCollector.objects.get_or_create(
            app_name="finance",
            defaults={"config": finance_config}
        )

        graph, _ = Graph.objects.get_or_create(
            name="FinanceGraph",
            defaults={"description": "Graph for Finance app", "collector": collector}
        )
        if graph.collector != collector:
            graph.collector = collector
            graph.save()

    def handle(self, *args, **options):
        """Run all steps in order."""
        self.create_collection_types()
        self.create_relation_types()
        self.create_collector_config()
        self.stdout.write(self.style.SUCCESS("✅ Finance graph schema + AppCollector created."))
