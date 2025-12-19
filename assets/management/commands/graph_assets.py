from django.core.management.base import BaseCommand
from ravioli.models import CollectionType, RelationType, Graph

class Command(BaseCommand):
    help = "Create graph schema and AppCollector + Graph for the assets app"

    # -----------------------------
    # Helpers
    # -----------------------------
    def ensure_collection_type(self, name, schema=None, layout=None):
        ct, created = CollectionType.objects.get_or_create(
            name=name,
            defaults={"json_schema": schema or {}, "form_layout": layout or {}}
        )
        if not created:
            ct.json_schema = schema or {}
            ct.form_layout = layout or {}
            ct.save()

    # -----------------------------
    # Collection Types
    # -----------------------------
    def create_collection_types(self):
        self.ensure_collection_type("AssetType")
        self.ensure_collection_type("Asset")
        self.ensure_collection_type("User")
        self.ensure_collection_type("Address")

    # -----------------------------
    # Relation Types
    # -----------------------------
    def create_relation_types(self):
        relations = [
            ("Asset-AssetType", {"from": "Asset", "to": "AssetType"}),
            ("Asset-User", {"from": "Asset", "to": "User"}),
            ("Asset-Location", {"from": "Asset", "to": "Address"}),
        ]

        for name, metadata in relations:
            RelationType.objects.get_or_create(
                name=name,
                defaults={"metadata": metadata}
            )

    # -----------------------------
    # Main handler
    # -----------------------------
    def handle(self, *args, **options):
        self.create_collection_types()
        self.create_relation_types()
        self.stdout.write(self.style.SUCCESS("✅ Asset graph schema + AppCollector created."))
