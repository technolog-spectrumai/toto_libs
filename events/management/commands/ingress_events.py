from oya.ingress import IngressCommand
from django.contrib.auth.models import User
from portfolio.models import Company
from events.models import EventCategory, Event
from django.utils.timezone import now
from faker import Faker
import random
from datetime import timedelta

fake = Faker()

class Command(IngressCommand):
    help = "Seed sample data for Events: categories, events, and registrations"

    def process(self, _):
        # 📅 Dashboard block
        self.create_dashboard_item(
            title="Events",
            icon="fa-solid fa-calendar-days",
            description="Manage and explore venture-related events",
            link="/events/calendar/"
        )
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
            cat, _ = EventCategory.objects.get_or_create(name=name, defaults={"description": desc})
            categories.append(cat)

        # 📦 Get ventures
        companies = list(Company.objects.all())
        if not companies:
            raise Exception("❌ No Companies found. Please seed companies first.")

        # 👥 Get users
        users = list(User.objects.all())
        if not users:
            raise Exception("❌ No users found. Please create some users first.")
        n_events = 12
        # 📅 Create Events
        for company in random.sample(companies, min(3, len(companies))):
            for i in range(n_events):
                start = now() + timedelta(days=random.randint(1, 30))
                end = start + timedelta(hours=random.randint(1, 5))
                event = Event.objects.create(
                    title=f"{company.name} {random.choice(['Summit', 'Bootcamp', 'Forum'])}",
                    description=fake.paragraph(nb_sentences=3),
                    location=fake.city(),
                    start_time=start,
                    end_time=end,
                    company=company,
                    organizer=random.choice(users),
                    category=random.choice(categories)
                )
                self.stdout.write(self.style.SUCCESS(f"📅 Created event: {event.title}"))

        self.stdout.write(self.style.SUCCESS("✅ Event seeding complete."))
