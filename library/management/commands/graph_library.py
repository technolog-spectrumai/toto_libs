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

    def handle(self, *args, **options):
        """Run all steps in order."""
        self.create_collection_types()
        self.create_relation_types()
        self.stdout.write(self.style.SUCCESS("✅ References graph schema + AppCollector created."))
