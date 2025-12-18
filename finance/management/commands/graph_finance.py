from django.core.management.base import BaseCommand
from ravioli.models import CollectionType, RelationType, AppCollector, Graph


class Command(BaseCommand):
    help = "Create graph schema and AppCollector + Graph for the finance app"

    def ensure_collection_type(self, name, schema=None, layout=None):
        """Create or update a CollectionType with optional schema/layout."""
        ct, created = CollectionType.objects.get_or_create(
            name=name,
            defaults={"json_schema": schema or {}, "form_layout": layout or {}}
        )
        if not created:
            ct.json_schema = schema or {}
            ct.form_layout = layout or {}
            ct.save()

    def create_collection_types(self):
        """Define all CollectionTypes for finance models."""
        self.ensure_collection_type("Currency")
        self.ensure_collection_type("ExchangeRate")
        self.ensure_collection_type("Account")
        self.ensure_collection_type("Transaction")
        self.ensure_collection_type("User")
        self.ensure_collection_type("CommunityMember")

    def create_relation_types(self):
        """Define all RelationTypes for finance models."""
        relations = [
            ("Base-Currency", {"from": "ExchangeRate", "to": "Currency"}),
            ("Quote-Currency", {"from": "ExchangeRate", "to": "Currency"}),
            ("Account-Owner", {"from": "Account", "to": "CommunityMember"}),
            ("Account-Manager", {"from": "Account", "to": "User"}),
            ("Account-Currency", {"from": "Account", "to": "Currency"}),
            ("Transaction-Currency", {"from": "Transaction", "to": "Currency"}),
            ("Transaction-Source", {"from": "Transaction", "to": "Account"}),
            ("Transaction-Destination", {"from": "Transaction", "to": "Account"}),
        ]
        for name, metadata in relations:
            RelationType.objects.get_or_create(name=name, defaults={"metadata": metadata})

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
