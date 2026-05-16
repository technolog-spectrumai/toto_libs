from toto.ingress import IngressCommand
from toto.bento.models import Category, IdeaBox, IdeaLink


class Command(IngressCommand):
    help = "Seed Bento with sample categories, idea boxes, and links (no topics)"

    def process(self):
        if not self.full:
            return

        self.stdout.write(self.style.WARNING("🍱 Seeding Bento…"))

        # 1) Create or find categories
        categories = {}
        category_data = [
            ("Principle", "A reusable rule of thumb or general insight."),
            ("Observation", "Something noticed while reading, watching, or thinking."),
            ("Question", "An open question worth returning to."),
            ("Technique", "A practical method or move."),
            ("Concept", "A concept-like idea box used as a local knowledge node."),
        ]
        for name, description in category_data:
            category, created = Category.objects.get_or_create(
                name=name,
                defaults={"description": description},
            )
            categories[name] = category
            self.stdout.write(
                self.style.SUCCESS(f"🏷️ Created category: {name}") if created else
                self.style.WARNING(f"ℹ️ Category already exists: {name}")
            )

        # 2) Create concept boxes
        storytelling_box = self._box(
            title="Storytelling",
            body="A way of structuring information as narrative.",
            is_concept=True,
            category=categories["Concept"],
            properties={"kind": "concept", "domain": "communication", "color": "orange"},
        )

        memory_box = self._box(
            title="Memory",
            body="The process of encoding, storing, and recalling information.",
            is_concept=True,
            category=categories["Concept"],
            properties={"kind": "concept", "domain": "psychology", "color": "blue"},
        )

        explanation_box = self._box(
            title="Explanation",
            body="Making something understandable by connecting it to what is already known.",
            is_concept=True,
            category=categories["Concept"],
            properties={"kind": "concept", "domain": "learning", "color": "green"},
        )

        # 3) Create normal idea boxes
        stories_beat_facts = self._box(
            title="Stories beat facts",
            body=(
                "People remember information better when it is wrapped in a story "
                "instead of presented as isolated facts."
            ),
            is_concept=False,
            category=categories["Principle"],
            source_title="Made to Stick",
            source_type="book",
            properties={"kind": "idea", "rating": 5, "status": "raw"},
        )

        examples_before_definitions = self._box(
            title="Examples before definitions",
            body=(
                "A difficult concept is often easier to understand when the learner "
                "first sees a concrete example."
            ),
            is_concept=False,
            category=categories["Technique"],
            source_title="Personal note",
            source_type="note",
            properties={"kind": "idea", "rating": 4, "status": "raw"},
        )

        compression = self._box(
            title="Good notes compress experience",
            body=(
                "A good note is not a transcript. It compresses an experience into "
                "something reusable."
            ),
            is_concept=False,
            category=categories["Principle"],
            source_title="Bento seed",
            source_type="note",
            properties={"kind": "idea", "rating": 5, "status": "processed"},
        )

        question = self._box(
            title="What makes an idea worth saving?",
            body=(
                "Maybe an idea is worth saving when it changes a future decision, "
                "explanation, design, or conversation."
            ),
            is_concept=False,
            category=categories["Question"],
            source_title="Bento seed",
            source_type="question",
            properties={"kind": "question", "rating": 3, "status": "open"},
        )

        # 4) Link ideas to concept boxes
        self._link(stories_beat_facts, storytelling_box, "about", {"strength": 0.95})
        self._link(stories_beat_facts, memory_box, "about", {"strength": 0.9})
        self._link(examples_before_definitions, explanation_box, "about", {"strength": 0.85})
        self._link(compression, explanation_box, "related to", {"strength": 0.7})

        # 5) Link ideas to ideas
        self._link(stories_beat_facts, examples_before_definitions, "supports", {
            "reason": "Both suggest that concrete structure improves understanding."
        })
        self._link(compression, stories_beat_facts, "related to", {
            "reason": "Both are about making information easier to reuse."
        })
        self._link(question, compression, "answered by", {
            "reason": "The compression idea gives one possible answer."
        })

        self.stdout.write(self.style.SUCCESS("✅ Bento seeding complete."))

    # ────────────────────────────────────────────────
    # Helper methods
    # ────────────────────────────────────────────────

    def _box(
        self,
        *,
        title,
        body,
        is_concept=False,
        category=None,
        source_title="",
        source_url="",
        source_type="",
        quote="",
        properties=None,
    ):
        box, created = IdeaBox.objects.get_or_create(
            title=title,
            defaults={
                "body": body,
                "is_concept": is_concept,
                "category": category,
                "source_title": source_title,
                "source_url": source_url,
                "source_type": source_type,
                "quote": quote,
                "properties": properties or {},
            },
        )

        # Update if needed
        updates = {
            "body": body,
            "is_concept": is_concept,
            "category": category,
            "source_title": source_title,
            "source_url": source_url,
            "source_type": source_type,
            "quote": quote,
            "properties": properties or {},
        }

        changed = False
        for field, value in updates.items():
            if getattr(box, field) != value:
                setattr(box, field, value)
                changed = True

        if changed:
            box.save()
            self.stdout.write(self.style.SUCCESS(f"🔁 Updated box: {title}"))
        elif created:
            kind = "concept" if is_concept else "idea"
            self.stdout.write(self.style.SUCCESS(f"💡 Created {kind}: {title}"))
        else:
            self.stdout.write(self.style.WARNING(f"ℹ️ Box already exists: {title}"))

        return box

    def _link(self, from_box, to_box, label, properties=None):
        link, created = IdeaLink.objects.get_or_create(
            from_box=from_box,
            to_box=to_box,
            label=label,
            defaults={"properties": properties or {}},
        )

        if not created:
            new_properties = properties or {}
            if link.properties != new_properties:
                link.properties = new_properties
                link.save(update_fields=["properties"])
                self.stdout.write(
                    self.style.SUCCESS(f"🔁 Updated link: {from_box} → {label} → {to_box}")
                )
            else:
                self.stdout.write(
                    self.style.WARNING(f"ℹ️ Link already exists: {from_box} → {label} → {to_box}")
                )
        else:
            self.stdout.write(
                self.style.SUCCESS(f"🔗 Linked: {from_box} → {label} → {to_box}")
            )
        return link