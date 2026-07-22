from datetime import timedelta
import random

from django.utils.timezone import now
from faker import Faker

from toto.ingress import IngressCommand
from toto.events.models import EventCategory, ScheduledEvent
from toto.locations.models import Address
from toto.people.models import Person


fake = Faker()


class Command(IngressCommand):
    help = "Seed sample data for Events: categories and scheduled events"

    def process(self):
        if not self.full:
            return

        categories = []
        category_names = [
            ("Conference", "Industry-wide gathering of professionals"),
            ("Workshop", "Hands-on training and learning sessions"),
            ("Webinar", "Online educational event"),
            ("Networking", "Meet and connect with peers"),
            ("Field Visit", "On-site visit connected to a real location"),
            ("Ceremony", "Formal organised ceremony"),
            ("Regional Briefing", "Coordination meeting for a region"),
        ]

        for name, desc in category_names:
            category, _ = EventCategory.objects.get_or_create(
                name=name,
                defaults={"description": desc},
            )
            categories.append(category)

        members = list(Person.objects.all())

        if not members:
            raise Exception("❌ No people found. Please create some first.")

        addresses = list(Address.objects.all())

        if not addresses:
            self.stdout.write(self.style.WARNING("⚠ No addresses found. Events will have no address."))

        companies = [fake.company() for _ in range(4)]
        event_templates = [
            ("Summit", "Conference"),
            ("Bootcamp", "Workshop"),
            ("Forum", "Networking"),
            ("Field Review", "Field Visit"),
            ("Ceremony", "Ceremony"),
            ("Regional Briefing", "Regional Briefing"),
        ]

        created_count = 0

        for company in random.sample(companies, min(3, len(companies))):
            for _ in range(12):
                start = now() + timedelta(days=random.randint(1, 30))
                end = start + timedelta(hours=random.randint(1, 5))

                suffix, preferred_category_name = random.choice(event_templates)
                category = next(
                    (c for c in categories if c.name == preferred_category_name),
                    random.choice(categories),
                )

                address = random.choice(addresses) if addresses else None

                organizer = random.choice(members)
                event = ScheduledEvent.objects.create(
                    title=f"{company} {suffix}",
                    description=fake.paragraph(nb_sentences=3),
                    start_time=start,
                    end_time=end,
                    owner=organizer,
                    category=category,
                    address=address,
                    public=True,
                    requires_registration=random.choice([True, False]),
                )
                event.organizers.add(organizer)

                created_count += 1

                self.stdout.write(
                    self.style.SUCCESS(
                        f"📅 Created: {event.title} · {event.start_time:%Y-%m-%d}"
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(f"✅ Event seeding complete. Created {created_count} events.")
        )
