from datetime import timedelta
import random

from django.utils.timezone import now
from faker import Faker

from toto.ingress import IngressCommand
from toto.events.models import EventCategory, Event
from toto.locations.models import Address, Route, Zone
from toto.socialhub.models import Person


fake = Faker()


class Command(IngressCommand):
    help = "Seed sample data for Events: categories and structured-location events"

    def process(self):
        if not self.full:
            return

        # 🎭 Event Categories
        categories = []
        category_names = [
            ("Conference", "Industry-wide gathering of professionals"),
            ("Workshop", "Hands-on training and learning sessions"),
            ("Webinar", "Online educational event"),
            ("Networking", "Meet and connect with peers"),
            ("Field Visit", "On-site visit connected to a real location"),
            ("Route Session", "Movement-based session connected to a route"),
            ("Regional Briefing", "Zone-scoped coordination meeting"),
        ]

        for name, desc in category_names:
            category, _ = EventCategory.objects.get_or_create(
                name=name,
                defaults={"description": desc},
            )
            categories.append(category)

        # 👥 Community members
        members = list(Person.objects.all())

        if not members:
            raise Exception("❌ No community members found. Please create some first.")

        # 🗺️ Existing structured locations
        addresses = list(Address.objects.all())
        routes = list(Route.objects.select_related("start_address", "end_address", "route_chain"))
        zones = list(Zone.objects.select_related("territory"))

        if not addresses:
            self.stdout.write(self.style.WARNING("⚠ No addresses found. Address-linked events will be skipped."))

        if not routes:
            self.stdout.write(self.style.WARNING("⚠ No routes found. Route-linked events will be skipped."))

        if not zones:
            self.stdout.write(self.style.WARNING("⚠ No zones found. Zone-linked events will be skipped."))

        companies = [fake.company() for _ in range(4)]

        event_templates = [
            ("Summit", "Conference"),
            ("Bootcamp", "Workshop"),
            ("Forum", "Networking"),
            ("Field Review", "Field Visit"),
            ("Route Walkthrough", "Route Session"),
            ("Zone Briefing", "Regional Briefing"),
        ]

        created_count = 0

        for company in random.sample(companies, min(3, len(companies))):
            for _ in range(12):
                start = now() + timedelta(days=random.randint(1, 30))
                end = start + timedelta(hours=random.randint(1, 5))

                suffix, preferred_category_name = random.choice(event_templates)
                category = next(
                    (cat for cat in categories if cat.name == preferred_category_name),
                    random.choice(categories),
                )

                address = None
                route = None
                zone = None

                # Choose location style based on category.
                if preferred_category_name == "Field Visit" and addresses:
                    address = random.choice(addresses)

                elif preferred_category_name == "Route Session" and routes:
                    route = random.choice(routes)

                elif preferred_category_name == "Regional Briefing" and zones:
                    zone = random.choice(zones)

                else:
                    # For generic events, attach one optional structured location when possible.
                    available_location_types = []

                    if addresses:
                        available_location_types.append("address")

                    if routes:
                        available_location_types.append("route")

                    if zones:
                        available_location_types.append("zone")

                    if available_location_types:
                        location_type = random.choice(available_location_types)

                        if location_type == "address":
                            address = random.choice(addresses)
                        elif location_type == "route":
                            route = random.choice(routes)
                        elif location_type == "zone":
                            zone = random.choice(zones)

                event = Event.objects.create(
                    title=f"{company} {suffix}",
                    description=fake.paragraph(nb_sentences=3),
                    start_time=start,
                    end_time=end,
                    organizer=random.choice(members),
                    category=category,
                    address=address,
                    route=route,
                    zone=zone,
                    public=True,
                )

                created_count += 1

                self.stdout.write(
                    self.style.SUCCESS(
                        f"📅 Created event: {event.title} · Location: {event.effective_location or '—'}"
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(f"✅ Event seeding complete. Created {created_count} events.")
        )