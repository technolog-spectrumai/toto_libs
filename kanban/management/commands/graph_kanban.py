from django.core.management.base import BaseCommand
from ravioli.models import CollectionType, RelationType, AppCollector, Graph


class Command(BaseCommand):
    help = "Create graph schema and AppCollector + Graph for the Kanban app"

    def create_collection_types(self):
        """Define CollectionTypes for Kanban models."""
        CollectionType.objects.get_or_create(name="Project")
        CollectionType.objects.get_or_create(name="Column")
        CollectionType.objects.get_or_create(name="Campaign")
        CollectionType.objects.get_or_create(name="Mission")
        CollectionType.objects.get_or_create(name="Sprint")
        CollectionType.objects.get_or_create(name="Task")
        CollectionType.objects.get_or_create(name="User")

    def create_relation_types(self):
        """Define RelationTypes for Kanban models."""
        RelationType.objects.get_or_create(
            name="Project-Owner",
            defaults={"metadata": {"from": "Project", "to": "User", "type": "owned_by"}}
        )
        RelationType.objects.get_or_create(
            name="Project-Collaborator",
            defaults={"metadata": {"from": "Project", "to": "User", "type": "collaborated_by"}}
        )
        RelationType.objects.get_or_create(
            name="Project-Column",
            defaults={"metadata": {"from": "Project", "to": "Column", "type": "has"}}
        )
        RelationType.objects.get_or_create(
            name="Project-Campaign",
            defaults={"metadata": {"from": "Project", "to": "Campaign", "type": "has"}}
        )
        RelationType.objects.get_or_create(
            name="Campaign-Mission",
            defaults={"metadata": {"from": "Campaign", "to": "Mission", "type": "has"}}
        )
        RelationType.objects.get_or_create(
            name="Mission-Task",
            defaults={"metadata": {"from": "Mission", "to": "Task", "type": "has"}}
        )
        RelationType.objects.get_or_create(
            name="Task-Column",
            defaults={"metadata": {"from": "Task", "to": "Column", "type": "in"}}
        )
        RelationType.objects.get_or_create(
            name="Task-Sprint",
            defaults={"metadata": {"from": "Task", "to": "Sprint", "type": "scheduled_in"}}
        )
        RelationType.objects.get_or_create(
            name="Task-Assignee",
            defaults={"metadata": {"from": "Task", "to": "User", "type": "assigned_to"}}
        )

    def create_collector_config(self):
        """Register AppCollector + Graph with descriptive config."""
        kanban_config = {
            "models": {
                "Project": {
                    "collection_type": "Project",
                    "fields": ["name", "description"],
                    "relations": {
                        "owner": "Project-Owner",
                        "collaborators": "Project-Collaborator",
                        "columns": "Project-Column",
                        "campaigns": "Project-Campaign",
                    }
                },
                "Column": {
                    "collection_type": "Column",
                    "fields": ["name", "position"],
                    "relations": {}
                },
                "Campaign": {
                    "collection_type": "Campaign",
                    "fields": ["name", "description", "start_date", "end_date"],
                    "relations": {
                        "missions": "Campaign-Mission",
                        "project": "Project-Campaign",
                        "owner": "Project-Owner",
                    }
                },
                "Mission": {
                    "collection_type": "Mission",
                    "fields": ["title", "description", "urgency", "impact"],
                    "relations": {
                        "tasks": "Mission-Task",
                        "campaign": "Campaign-Mission",
                        "owner": "Task-Assignee",
                    }
                },
                "Sprint": {
                    "collection_type": "Sprint",
                    "fields": ["name", "start_time", "end_time"],
                    "relations": {}
                },
                "Task": {
                    "collection_type": "Task",
                    "fields": ["title", "description", "position", "weight", "completed_at"],
                    "relations": {
                        "mission": "Mission-Task",
                        "column": "Task-Column",
                        "sprint": "Task-Sprint",
                        "assignee": "Task-Assignee",
                    }
                },
                "User": {
                    "collection_type": "User",
                    "fields": ["username", "email"],
                    "relations": {}
                }
            }
        }

        collector, _ = AppCollector.objects.get_or_create(
            app_name="kanban",
            defaults={"config": kanban_config}
        )

        graph, _ = Graph.objects.get_or_create(
            name="KanbanGraph",
            defaults={"description": "Graph for Kanban app", "collector": collector}
        )
        if graph.collector != collector:
            graph.collector = collector
            graph.save()

    def handle(self, *args, **options):
        """Run all steps in order."""
        self.create_collection_types()
        self.create_relation_types()
        self.create_collector_config()
        self.stdout.write(self.style.SUCCESS("✅ Kanban graph schema + AppCollector created."))
