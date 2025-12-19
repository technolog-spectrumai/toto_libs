from django.core.management.base import BaseCommand
from ravioli.models import CollectionType, RelationType, Graph


class Command(BaseCommand):
    help = "Create graph schema and AppCollector + Graph for the references app"

    def create_collection_types(self):
        """Define CollectionTypes for reference models."""
        for name in [
            "ReferenceItem", "ReferenceTag",
            "BookReference", "JournalReference",
            "VideoReference", "AudioReference",
            "WebsiteReference", "GenericReference"
        ]:
            CollectionType.objects.get_or_create(
                name=name,
                defaults={"json_schema": {}, "form_layout": {}}
            )

    def create_relation_types(self):
        """Define RelationTypes for reference models."""
        relations = [
            ("ReferenceItem-Tag", {"from": "ReferenceItem", "to": "ReferenceTag", "type": "tagged_with"}),
            ("ReferenceItem-File", {"from": "ReferenceItem", "to": "VaultFile", "type": "linked_file"}),
            # Polymorphic subclasses are still ReferenceItems, so no extra relations needed
        ]
        for name, metadata in relations:
            RelationType.objects.get_or_create(name=name, defaults={"metadata": metadata})

    def create_collector_config(self):
        """Register AppCollector + Graph with descriptive config."""
        references_config = {
            "models": {
                "ReferenceItem": {
                    "collection_type": "ReferenceItem",
                    "fields": ["title", "order"],
                    "relations": {
                        "tags": "ReferenceItem-Tag",
                        "vault_file": "ReferenceItem-File"
                    }
                },
                "ReferenceTag": {
                    "collection_type": "ReferenceTag",
                    "fields": ["name"],
                    "relations": {}
                },
                "BookReference": {
                    "collection_type": "BookReference",
                    "fields": ["author", "publisher", "year", "isbn"],
                    "relations": {}
                },
                "JournalReference": {
                    "collection_type": "JournalReference",
                    "fields": ["author", "journal", "volume", "issue", "pages", "year", "doi"],
                    "relations": {}
                },
                "VideoReference": {
                    "collection_type": "VideoReference",
                    "fields": ["creator", "platform", "url", "year"],
                    "relations": {}
                },
                "AudioReference": {
                    "collection_type": "AudioReference",
                    "fields": ["artist", "album", "url", "year"],
                    "relations": {}
                },
                "WebsiteReference": {
                    "collection_type": "WebsiteReference",
                    "fields": ["author", "sitename", "url", "accessed_date", "year"],
                    "relations": {}
                },
                "GenericReference": {
                    "collection_type": "GenericReference",
                    "fields": ["description", "url", "year", "author", "sourcetype"],
                    "relations": {}
                }
            }
        }

    def handle(self, *args, **options):
        """Run all steps in order."""
        self.create_collection_types()
        self.create_relation_types()
        self.create_collector_config()
        self.stdout.write(self.style.SUCCESS("✅ References graph schema + AppCollector created."))
