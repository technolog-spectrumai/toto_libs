from django.core.management.base import BaseCommand
from ravioli.models import CollectionType, RelationType, Graph


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


    def handle(self, *args, **options):
        """Run all steps in order."""
        self.create_collection_types()
        self.create_relation_types()
        self.stdout.write(self.style.SUCCESS("✅ Finance graph schema + AppCollector created."))
