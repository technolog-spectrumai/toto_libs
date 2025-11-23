import random
from django.utils.timezone import now
from faker import Faker
from oya.ingress import IngressCommand
from notes.models import Note, Tag
from community.models import CommunityMember, Community
from portfolio.models import Company
from events.models import Event
from rest_framework.reverse import reverse_lazy
from django.contrib.auth.models import User   # ✅ import User

fake = Faker()


class Command(IngressCommand):
    help = "Creates demo Intel Notes with sample notes, tags, subjects, and events"

    def process(self):
        # 📊 Dashboard block
        self.create_dashboard_item(
            title="Intel Notes",
            icon="fa-solid fa-note-sticky",
            description="A demo graph of intel notes, tags, subjects, and events.",
            link=reverse_lazy("notes:public_notes_list"),
            public=False,
        )

        if not self.full:
            return

        # Step 0: Ensure we have at least one user
        try:
            author = User.objects.first()
            if not author:
                raise Exception("❌ No users found. Please create at least one User before running ingress.")
        except User.DoesNotExist:
            raise Exception("❌ No users found. Please create at least one User before running ingress.")

        # Step 1: Create Tags
        tags = []
        tag_names = ["Security", "Finance", "Operations", "Intel", "Observation"]
        for name in tag_names:
            tag, created = Tag.objects.get_or_create(name=name)
            tags.append(tag)
            if created:
                self.stdout.write(self.style.SUCCESS(f"🏷️ Created tag: {name}"))

        # Step 2: Collect subjects and events
        subjects = []
        subjects.extend(list(CommunityMember.objects.all()[:5]))
        subjects.extend(list(Community.objects.all()[:5]))
        subjects.extend(list(Company.objects.all()[:5]))

        events = list(Event.objects.all()[:5])

        if not subjects and not events:
            self.stdout.write(self.style.WARNING(
                "⚠️ No subjects or events found. Notes will have no subject/event."
            ))

        # Step 3: Create Notes
        notes = []
        for i in range(5):
            subject_choice = random.choice(subjects) if subjects else None
            event_choice = random.choice(events) if events else None
            note = Note.objects.create(
                title=fake.sentence(nb_words=6),
                content=fake.paragraph(nb_sentences=3),
                metadata={"source": "demo", "confidence": random.choice(["high", "medium", "low"])},
                created_at=now(),
                category=random.choice(["Intel", "Observation", "Report"]),
                subject=subject_choice,
                event=event_choice,
                author=author,   # ✅ assign author
            )
            notes.append(note)
            self.stdout.write(self.style.SUCCESS(
                f"📝 Created note: {note.title} (author={author.username}, subject={subject_choice}, event={event_choice})"
            ))

        # Step 4: Assign Tags randomly
        for note in notes:
            chosen_tags = random.sample(tags, k=random.randint(1, 3))
            note.tags.add(*chosen_tags)
            self.stdout.write(self.style.SUCCESS(
                f"🔗 Tagged note '{note.title}' with {[t.name for t in chosen_tags]}"
            ))

        self.stdout.write(self.style.SUCCESS("✅ Demo Intel Notes ingress complete."))
