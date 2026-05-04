from toto.core.ingress import IngressCommand
from toto.events.models import EventCategory, Event
from toto.socialhub.models import CommunityMember
from django.utils.timezone import now
from faker import Faker
import random
from datetime import timedelta


fake = Faker()


class Command(IngressCommand):
    help = "Seed sample data for Events: categories, events, and registrations"

    def process(self):

        if not self.full:
            return

        # 🎭 Create Event Categories
        categories = []
        category_names = [
            ("Conference", "Industry-wide gathering of professionals"),
            ("Workshop", "Hands-on training and learning sessions"),
            ("Webinar", "Online educational event"),
            ("Networking", "Meet and connect with peers")
        ]

        for name, desc in category_names:
            cat, _ = EventCategory.objects.get_or_create(
                name=name,
                defaults={"description": desc}
            )
            categories.append(cat)

        # 👥 Get community members (NOT Users anymore)
        members = list(CommunityMember.objects.all())
        if not members:
            raise Exception("❌ No community members found. Please create some first.")

        # 📅 Create Events
        n_events = 12
        companies = [fake.company() for _ in range(4)]

        for company in random.sample(companies, min(3, len(companies))):
            for i in range(n_events):
                start = now() + timedelta(days=random.randint(1, 30))
                end = start + timedelta(hours=random.randint(1, 5))

                event = Event.objects.create(
                    title=f"{company} {random.choice(['Summit', 'Bootcamp', 'Forum'])}",
                    description=fake.paragraph(nb_sentences=3),
                    location=fake.city(),
                    start_time=start,
                    end_time=end,
                    organizer=random.choice(members),  # ✅ FIXED
                    category=random.choice(categories),
                    public=True
                )

                self.stdout.write(
                    self.style.SUCCESS(f"📅 Created event: {event.title}")
                )

        self.stdout.write(self.style.SUCCESS("✅ Event seeding complete."))
