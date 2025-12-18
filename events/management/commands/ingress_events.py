from oya.ingress import IngressCommand
from django.contrib.auth.models import User
from events.models import EventCategory, Event
from django.utils.timezone import now
from faker import Faker
import random
from datetime import timedelta
from ravioli.models import CollectionType, RelationType


fake = Faker()


class Command(IngressCommand):
    help = "Seed sample data for Events: categories, events, and registrations"

    def create_graph_config(self):
        """
        Define CollectionTypes and RelationTypes for EventCategory and Event models.
        """
        # 📂 CollectionType: EventCategory
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

        # 📂 CollectionType: Event
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

        # 🔗 RelationTypes
        RelationType.objects.get_or_create(
            name="Event-Category",
            defaults={"metadata": {"from": "Event", "to": "EventCategory", "type": "belongs_to"}}
        )
        RelationType.objects.get_or_create(
            name="Event-Organizer",
            defaults={"metadata": {"from": "Event", "to": "User", "type": "organized_by"}}
        )

        self.stdout.write(self.style.SUCCESS("✅ Graph config created (collections + relations)."))

    def process(self):
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


        # 👥 Get users
        users = list(User.objects.all())
        if not users:
            raise Exception("❌ No users found. Please create some users first.")
        n_events = 12
        # 📅 Create Events
        companies = [
            fake.company() for _ in range(4)
        ]
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
                    organizer=random.choice(users),
                    category=random.choice(categories)
                )
                self.stdout.write(self.style.SUCCESS(f"📅 Created event: {event.title}"))
        self.create_graph_config()
        self.stdout.write(self.style.SUCCESS("✅ Event seeding complete."))
