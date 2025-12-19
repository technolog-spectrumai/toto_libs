from django.core.management.base import BaseCommand
from ravioli.models import CollectionType, RelationType, Graph


class Command(BaseCommand):
    help = "Create graph schema and AppCollector + Graph for the locations app"

    def create_collection_types(self):
        """Define CollectionTypes for location models."""
        for name in ["PointFeature", "ZoneFeature", "PathFeature", "Address"]:
            CollectionType.objects.get_or_create(
                name=name,
                defaults={"json_schema": {}, "form_layout": {}}
            )

    def create_relation_types(self):
        """Define RelationTypes for location models."""
        relations = [
            # Address has a point location
            ("Address-Location", {"from": "Address", "to": "PointFeature", "type": "located_at"}),
            # PathFeature may cross ZoneFeature
            ("Path-Zone", {"from": "PathFeature", "to": "ZoneFeature", "type": "crosses"}),
            # PointFeature may be inside ZoneFeature
            ("Point-Zone", {"from": "PointFeature", "to": "ZoneFeature", "type": "inside"}),
        ]
        for name, metadata in relations:
            RelationType.objects.get_or_create(name=name, defaults={"metadata": metadata})

    def create_collector_config(self):
        """Register AppCollector + Graph with descriptive config."""
        locations_config = {
            "models": {
                "PointFeature": {
                    "collection_type": "PointFeature",
                    "fields": ["name", "geometry"],
                    "relations": {"zone": "Point-Zone"}
                },
                "ZoneFeature": {
                    "collection_type": "ZoneFeature",
                    "fields": ["name", "geometry"],
                    "relations": {}
                },
                "PathFeature": {
                    "collection_type": "PathFeature",
                    "fields": ["name", "geometry"],
                    "relations": {"zone": "Path-Zone"}
                },
                "Address": {
                    "collection_type": "Address",
                    "fields": [
                        "country_name", "state_or_province_name", "locality_name",
                        "street", "building", "apartment", "location"
                    ],
                    "relations": {"location": "Address-Location"}
                }
            }
        }

    def handle(self, *args, **options):
        """Run all steps in order."""
        self.create_collection_types()
        self.create_relation_types()
        self.create_collector_config()
        self.stdout.write(self.style.SUCCESS("✅ Locations graph schema + AppCollector created."))
