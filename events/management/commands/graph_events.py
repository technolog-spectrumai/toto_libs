from django.core.management.base import BaseCommand
from ravioli.models import CollectionType, RelationType, AppCollector, Graph


class Command(BaseCommand):
    help = "Create graph schema and AppCollector + Graph for the events app"

    def create_collection_types(self):
        """Define CollectionTypes for EventCategory and Event models."""
        event_category_schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "maxLength": 100},
                "description": {"type": "string"}
            },
            "required": ["name"]
        }
        event_category_layout = {
            "fields": [
                {"name": "name", "widget": "text"},
                {"name": "description", "widget": "textarea"}
            ]
        }
        CollectionType.objects.get_or_create(
            name="EventCategory",
            defaults={"json_schema": event_category_schema, "form_layout": event_category_layout}
        )

        event_schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string", "maxLength": 200},
                "description": {"type": "string"},
                "location": {"type": "string", "maxLength": 200},
                "start_time": {"type": "string", "format": "date-time"},
                "end_time": {"type": "string", "format": "date-time"},
                "public": {"type": "boolean"}
            },
            "required": ["title", "start_time", "end_time"]
        }
        event_layout = {
            "fields": [
                {"name": "title", "widget": "text"},
                {"name": "description", "widget": "textarea"},
                {"name": "location", "widget": "text"},
                {"name": "start_time", "widget": "datetime"},
                {"name": "end_time", "widget": "datetime"},
                {"name": "public", "widget": "checkbox"}
            ]
        }
        CollectionType.objects.get_or_create(
            name="Event",
            defaults={"json_schema": event_schema, "form_layout": event_layout}
        )

    def create_relation_types(self):
        """Define RelationTypes for events."""
        RelationType.objects.get_or_create(
            name="Event-Category",
            defaults={"metadata": {"from": "Event", "to": "EventCategory", "type": "belongs_to"}}
        )
        RelationType.objects.get_or_create(
            name="Event-Organizer",
            defaults={"metadata": {"from": "Event", "to": "User", "type": "organized_by"}}
        )

    def create_collector_config(self):
        """Register AppCollector + Graph with descriptive config."""
        events_config = {
            "models": {
                "EventCategory": {
                    "collection_type": "EventCategory",
                    "fields": ["name", "description"],
                    "relations": {}
                },
                "Event": {
                    "collection_type": "Event",
                    "fields": [
                        "title", "description", "location",
                        "start_time", "end_time", "public"
                    ],
                    "relations": {
                        "category": "Event-Category",
                        "organizer": "Event-Organizer"
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
            app_name="events",
            defaults={"config": events_config}
        )

        graph, _ = Graph.objects.get_or_create(
            name="EventsGraph",
            defaults={"description": "Graph for Events app", "collector": collector}
        )
        if graph.collector != collector:
            graph.collector = collector
            graph.save()

    def handle(self, *args, **options):
        """Run all steps in order."""
        self.create_collection_types()
        self.create_relation_types()
        self.create_collector_config()
        self.stdout.write(self.style.SUCCESS("✅ Events graph schema + AppCollector created."))
