from django.core.management.base import BaseCommand
from ravioli.models import CollectionType, RelationType, Graph


class Command(BaseCommand):
    help = "Create graph schema and AppCollector + Graph for the Kanban app"

    def ensure_collection_type(self, name):
        """Create or update a CollectionType with optional schema/layout."""
        CollectionType.objects.get_or_create(
            name=name,
            defaults={"json_schema": {}, "form_layout": {}}
        )

    def create_collection_types(self):
        """Define CollectionTypes for Kanban models."""
        for name in ["Project", "Column", "Campaign", "Mission", "Sprint", "Task", "User"]:
            self.ensure_collection_type(name)

    def create_relation_types(self):
        """Define RelationTypes for Kanban models."""
        relations = [
            ("Project-Owner", {"from": "Project", "to": "User", "type": "owned_by"}),
            ("Project-Collaborator", {"from": "Project", "to": "User", "type": "collaborated_by"}),
            ("Project-Column", {"from": "Project", "to": "Column", "type": "has"}),
            ("Project-Campaign", {"from": "Project", "to": "Campaign", "type": "has"}),
            ("Campaign-Mission", {"from": "Campaign", "to": "Mission", "type": "has"}),
            ("Mission-Task", {"from": "Mission", "to": "Task", "type": "has"}),
            ("Task-Column", {"from": "Task", "to": "Column", "type": "in"}),
            ("Task-Sprint", {"from": "Task", "to": "Sprint", "type": "scheduled_in"}),
            ("Task-Assignee", {"from": "Task", "to": "User", "type": "assigned_to"}),
        ]
        for name, metadata in relations:
            RelationType.objects.get_or_create(name=name, defaults={"metadata": metadata})

    def handle(self, *args, **options):
        """Run all steps in order."""
        self.create_collection_types()
        self.create_relation_types()
        self.stdout.write(self.style.SUCCESS("✅ Kanban graph schema + AppCollector created."))
