import random
from django.utils.timezone import now
from faker import Faker
from oya.ingress import IngressCommand
from ravioli.models import Note, Tag
from community.models import CommunityMember, Address
from portfolio.models import Company

fake = Faker()


class Command(IngressCommand):
    help = "Creates demo Intel Notes with sample notes, tags, and subjects"

    def process(self, _):
        # 📊 Dashboard block
        self.create_dashboard_item(
            title="Intel Notes",
            icon="fa-solid fa-note-sticky",
            description="A demo graph of intel notes, tags, and subjects.",
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

        subjects = []
        subjects.extend(list(CommunityMember.objects.all()[:5]))
        subjects.extend(list(Company.objects.all()[:5]))

        if not subjects:
            self.stdout.write(self.style.WARNING("⚠️ No subjects found (CommunityMember/Address/Event). Notes will have no subject."))

        # Step 3: Create Notes
        notes = []
        for i in range(5):
            subject_choice = random.choice(subjects) if subjects else None
            note = Note.objects.create(
                title=fake.sentence(nb_words=6),
                content=fake.paragraph(nb_sentences=3),
                metadata={"source": "demo", "confidence": random.choice(["high", "medium", "low"])},
                created_at=now(),
                category=random.choice(["Intel", "Observation", "Report"]),
                subject=subject_choice if subject_choice else None,
            )
            notes.append(note)
            self.stdout.write(self.style.SUCCESS(f"📝 Created note: {note.title}"))

        # Step 4: Assign Tags randomly
        for note in notes:
            chosen_tags = random.sample(tags, k=random.randint(1, 3))
            note.tags.add(*chosen_tags)
            self.stdout.write(self.style.SUCCESS(
                f"🔗 Tagged note '{note.title}' with {[t.name for t in chosen_tags]}"
            ))

        self.stdout.write(self.style.SUCCESS("✅ Demo Intel Notes ingress complete."))
