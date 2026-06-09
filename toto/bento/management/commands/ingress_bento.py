from toto.ingress import IngressCommand
from toto.bento.models import BentoCategory, BentoEdgeType


class Command(IngressCommand):
    help = "Seed Bento with example node-category and edge-type templates."

    def process(self):
        if not self.full:
            return

        self.stdout.write(self.style.WARNING("🍱 Seeding Bento templates…"))

        categories = {
            "idea": {
                "name": "Idea",
                "neo4j_label": "Idea",
                "description": "A reusable idea, principle, or insight.",
                "property_schema": [
                    {"name": "title", "type": "string", "required": True, "label": "Title"},
                    {"name": "body", "type": "text", "required": False, "label": "Body"},
                    {"name": "rating", "type": "integer", "required": False, "label": "Rating"},
                ],
            },
            "source": {
                "name": "Source",
                "neo4j_label": "Source",
                "description": "A book, article, or note an idea came from.",
                "property_schema": [
                    {"name": "title", "type": "string", "required": True, "label": "Title"},
                    {"name": "url", "type": "string", "required": False, "label": "URL"},
                    {"name": "kind", "type": "string", "required": False, "label": "Kind"},
                ],
            },
            "question": {
                "name": "Question",
                "neo4j_label": "Question",
                "description": "An open question worth returning to.",
                "property_schema": [
                    {"name": "title", "type": "string", "required": True, "label": "Question"},
                    {"name": "status", "type": "string", "required": False, "label": "Status"},
                ],
            },
        }

        cat_objs = {}
        for slug, data in categories.items():
            obj, created = BentoCategory.objects.get_or_create(
                slug=slug, defaults=data
            )
            cat_objs[slug] = obj
            self.stdout.write(
                self.style.SUCCESS(f"🏷️ Created category template: {obj.name}")
                if created
                else self.style.WARNING(f"ℹ️ Category template exists: {obj.name}")
            )

        edge_types = [
            {
                "slug": "supports", "name": "Supports", "rel_type": "SUPPORTS",
                "property_schema": [
                    {"name": "strength", "type": "float", "required": False, "label": "Strength"},
                ],
                "sources": ["idea"], "targets": ["idea"],
            },
            {
                "slug": "about", "name": "About", "rel_type": "ABOUT",
                "property_schema": [],
                "sources": ["idea", "question"], "targets": ["source"],
            },
            {
                "slug": "answered-by", "name": "Answered by", "rel_type": "ANSWERED_BY",
                "property_schema": [],
                "sources": ["question"], "targets": ["idea"],
            },
        ]

        for data in edge_types:
            sources = data.pop("sources", [])
            targets = data.pop("targets", [])
            obj, created = BentoEdgeType.objects.get_or_create(
                slug=data["slug"], defaults=data
            )
            obj.allowed_sources.set([cat_objs[s] for s in sources if s in cat_objs])
            obj.allowed_targets.set([cat_objs[t] for t in targets if t in cat_objs])
            self.stdout.write(
                self.style.SUCCESS(f"🔗 Created edge-type template: {obj.name}")
                if created
                else self.style.WARNING(f"ℹ️ Edge-type template exists: {obj.name}")
            )

        self.stdout.write(self.style.SUCCESS("✅ Bento template seeding complete."))
