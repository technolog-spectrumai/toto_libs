import random
from django.utils.timezone import now
from faker import Faker

from oya.ingress import IngressCommand
from ravioli.models import Note, Tag

fake = Faker()


class Command(IngressCommand):
    help = "Creates demo Intel Notes with sample notes and tags"

    def process(self, _):
        # 📊 Dashboard block
        self.create_dashboard_item(
            title="Intel Notes",
            icon="fa-solid fa-note-sticky",
            description="A demo graph of intel notes and tags.",
            link="/ravioli/notes/"
        )

        if not self.full:
            return

        # Step 1: Create Tags
        tags = []
        tag_names = ["Security", "Finance", "Operations", "Intel", "Observation"]
        for name in tag_names:
            tag, created = Tag.objects.get_or_create(name=name)
            tags.append(tag)
            if created:
                self.stdout.write(self.style.SUCCESS(f"🏷️ Created tag: {name}"))

        # Step 2: Create Notes
        notes = []
        for i in range(5):
            note = Note.objects.create(
                title=fake.sentence(nb_words=6),
                content=fake.paragraph(nb_sentences=3),
                metadata={"source": "demo", "confidence": random.choice(["high", "medium", "low"])},
                created_at=now(),
                category=random.choice(["Intel", "Observation", "Report"]),
            )
            notes.append(note)
            self.stdout.write(self.style.SUCCESS(f"📝 Created note: {note.title}"))

        # Step 3: Assign Tags randomly
        for note in notes:
            chosen_tags = random.sample(tags, k=random.randint(1, 3))
            note.tags.add(*chosen_tags)
            self.stdout.write(self.style.SUCCESS(
                f"🔗 Tagged note '{note.title}' with {[t.name for t in chosen_tags]}"
            ))

        # Step 4: Relate Notes randomly
        for note in notes:
            related_notes = random.sample([n for n in notes if n != note], k=random.randint(1, 2))
            note.related.add(*related_notes)
            self.stdout.write(self.style.SUCCESS(
                f"🔗 Related note '{note.title}' to {[n.title for n in related_notes]}"
            ))

        self.stdout.write(self.style.SUCCESS("✅ Demo Intel Notes ingress complete."))
