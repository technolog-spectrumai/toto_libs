from datetime import timedelta

from django.utils import timezone

from toto.ingress import IngressCommand
from toto.locations.models import Address, Route
from toto.socialhub.models import Person
from toto.travels.models import Travel, Visit


class Command(IngressCommand):
    help = "Seeds demo Travel and Visit data linked to existing routes and addresses."

    def upsert_travel(
        self,
        route_name,
        participant_keys,
        starts_at,
        ends_at,
        info="",
        score=None,
        reviewed_at=None,
    ):
        route = self.routes.get(route_name)
        if not route:
            return

        travel, _ = Travel.objects.update_or_create(
            route=route,
            starts_at=starts_at,
            defaults={
                "ends_at": ends_at,
                "info": info,
                "score": score,
                "reviewed_at": reviewed_at,
            },
        )

        travel.participants.set(
            [self.people[key] for key in participant_keys if key in self.people]
        )

        return travel

    def upsert_visit(
        self,
        participant_key,
        location_key,
        score=None,
        review="",
        visited_at=None,
        reviewed_at=None,
    ):
        participant = self.people.get(participant_key)
        location = self.addresses.get(location_key)

        if not participant or not location:
            return

        visit, _ = Visit.objects.update_or_create(
            participant=participant,
            location=location,
            defaults={
                "visited_at": visited_at,
                "score": score,
                "reviewed_at": reviewed_at,
                "review": review,
            },
        )

        return visit

    def load_people(self):
        slugs = ["alice-martin", "jan-kowalski", "emma-smith", "marie-dubois"]
        people = list(
            Person.objects
            .exclude(display_name__isnull=True)
            .exclude(display_name="")
            .order_by("id")[:len(slugs)]
        )
        for slug, person in zip(slugs, people):
            self.people[slug] = person

    def load_routes(self):
        for route in Route.objects.all():
            self.routes[route.name] = route

    def load_addresses(self):
        for address in Address.objects.all():
            key = address.street.lower().replace(" ", "_").replace("-", "_").replace("'", "")
            self.addresses[key] = address

        address_aliases = {
            "eiffel_tower": "eiffel tower",
            "notre_dame": "notre-dame cathedral",
            "louvre": "louvre museum",
            "mont_saint_michel": "mont saint-michel abbey",
            "omaha_beach": "omaha beach",
            "tintagel_castle": "tintagel castle",
            "stonehenge": "stonehenge",
            "warsaw_royal_castle": "royal castle",
            "wawel_castle": "wawel castle",
            "long_market": "long market",
            "westerplatte": "westerplatte",
        }

        for key, street in address_aliases.items():
            match = Address.objects.filter(street__iexact=street).first()
            if match:
                self.addresses[key] = match

    def create_travels(self):
        now = timezone.now()

        travel_data = [
            {
                "route_name": "Paris Monument Walk",
                "participant_keys": ("alice-martin", "marie-dubois"),
                "starts_at": now - timedelta(days=18, hours=10),
                "ends_at": now - timedelta(days=18, hours=14),
                "score": 5,
                "reviewed_at": now - timedelta(days=17, hours=20),
                "info": (
                    "Excellent city walk. The route felt coherent, easy to follow, "
                    "and the transition from the Eiffel Tower toward Notre-Dame gave "
                    "a strong sense of Parisian scale."
                ),
            },
            {
                "route_name": "Paris Monument Walk",
                "participant_keys": ("emma-smith",),
                "starts_at": now - timedelta(days=10, hours=9),
                "ends_at": now - timedelta(days=10, hours=13),
                "score": 4,
                "reviewed_at": now - timedelta(days=9, hours=18),
                "info": (
                    "Beautiful route with strong landmarks. It would benefit from one "
                    "extra pause point near the Louvre for orientation and rest."
                ),
            },
            {
                "route_name": "Paris Monument Walk",
                "participant_keys": ("alice-martin", "jan-kowalski"),
                "starts_at": now + timedelta(days=3, hours=10),
                "ends_at": now + timedelta(days=3, hours=14),
                "score": None,
                "reviewed_at": None,
                "info": "A planned guided walk through major Paris landmarks.",
            },
            {
                "route_name": "Normandy Heritage Line",
                "participant_keys": ("alice-martin", "emma-smith"),
                "starts_at": now - timedelta(days=22, hours=9),
                "ends_at": now - timedelta(days=22, hours=18),
                "score": 5,
                "reviewed_at": now - timedelta(days=21, hours=11),
                "info": (
                    "Deeply memorable heritage route. The distance is substantial, "
                    "but the movement from Mont Saint-Michel toward Omaha Beach makes "
                    "the story feel grounded."
                ),
            },
            {
                "route_name": "Normandy Heritage Line",
                "participant_keys": ("marie-dubois",),
                "starts_at": now - timedelta(days=12, hours=8),
                "ends_at": now - timedelta(days=12, hours=17),
                "score": 4,
                "reviewed_at": now - timedelta(days=11, hours=10),
                "info": (
                    "Strong historical route. Good for a full-day visit, though the "
                    "schedule should leave more buffer for walking and reflection."
                ),
            },
            {
                "route_name": "Normandy Heritage Line",
                "participant_keys": ("alice-martin", "emma-smith"),
                "starts_at": now + timedelta(days=7, hours=9),
                "ends_at": now + timedelta(days=7, hours=18),
                "score": None,
                "reviewed_at": None,
                "info": "A planned heritage route from Mont Saint-Michel toward Omaha Beach.",
            },
            {
                "route_name": "Brittany Coast and Stones",
                "participant_keys": ("marie-dubois", "emma-smith"),
                "starts_at": now - timedelta(days=16, hours=8),
                "ends_at": now - timedelta(days=16, hours=18),
                "score": 4,
                "reviewed_at": now - timedelta(days=15, hours=12),
                "info": (
                    "A poetic route with excellent landscape changes. Carnac was a strong "
                    "ending point, but travel time between points should be made explicit."
                ),
            },
            {
                "route_name": "Brittany Coast and Stones",
                "participant_keys": ("alice-martin",),
                "starts_at": now - timedelta(days=6, hours=10),
                "ends_at": now - timedelta(days=6, hours=17),
                "score": 3,
                "reviewed_at": now - timedelta(days=5, hours=9),
                "info": (
                    "Good route concept, but it felt less polished than the Paris and Normandy "
                    "routes. More intermediate stops would help."
                ),
            },
            {
                "route_name": "Provence to Riviera",
                "participant_keys": ("marie-dubois", "alice-martin"),
                "starts_at": now - timedelta(days=20, hours=9),
                "ends_at": now - timedelta(days=20, hours=19),
                "score": 5,
                "reviewed_at": now - timedelta(days=19, hours=14),
                "info": (
                    "Excellent sun-chain route. Avignon, Aix, Cannes, and Nice together make "
                    "a clear cultural and coastal progression."
                ),
            },
            {
                "route_name": "Provence to Riviera",
                "participant_keys": ("jan-kowalski",),
                "starts_at": now - timedelta(days=8, hours=8),
                "ends_at": now - timedelta(days=8, hours=18),
                "score": 4,
                "reviewed_at": now - timedelta(days=7, hours=16),
                "info": (
                    "Very enjoyable route. The Riviera segment was strongest; Avignon needs "
                    "more time if the route is treated as a single-day itinerary."
                ),
            },
            {
                "route_name": "Arthurian South England",
                "participant_keys": ("emma-smith",),
                "starts_at": now - timedelta(days=14, hours=8),
                "ends_at": now - timedelta(days=14, hours=17),
                "score": 5,
                "reviewed_at": now - timedelta(days=13, hours=10),
                "info": (
                    "Atmospheric and memorable. Tintagel gives the route a strong beginning, "
                    "and Stonehenge works well as a monumental endpoint."
                ),
            },
            {
                "route_name": "Arthurian South England",
                "participant_keys": ("emma-smith", "marie-dubois"),
                "starts_at": now + timedelta(days=12, hours=8),
                "ends_at": now + timedelta(days=12, hours=17),
                "score": None,
                "reviewed_at": None,
                "info": "A planned mythic route from Tintagel Castle toward Stonehenge.",
            },
            {
                "route_name": "Polish Royal Trail",
                "participant_keys": ("jan-kowalski", "alice-martin"),
                "starts_at": now - timedelta(days=24, hours=9),
                "ends_at": now - timedelta(days=24, hours=20),
                "score": 5,
                "reviewed_at": now - timedelta(days=23, hours=13),
                "info": (
                    "A strong royal-history route. Warsaw and Krakow create a clear national "
                    "heritage arc, and the route works well as a flagship Polish trail."
                ),
            },
            {
                "route_name": "Polish Royal Trail",
                "participant_keys": ("emma-smith",),
                "starts_at": now - timedelta(days=9, hours=9),
                "ends_at": now - timedelta(days=9, hours=19),
                "score": 4,
                "reviewed_at": now - timedelta(days=8, hours=11),
                "info": (
                    "Very good route with impressive anchors. It needs clearer transport notes "
                    "between Warsaw and Krakow."
                ),
            },
            {
                "route_name": "Polish Royal Trail",
                "participant_keys": ("jan-kowalski", "alice-martin"),
                "starts_at": now + timedelta(days=15, hours=9),
                "ends_at": now + timedelta(days=15, hours=20),
                "score": None,
                "reviewed_at": None,
                "info": "A planned royal landmark route between Warsaw and Krakow.",
            },
            {
                "route_name": "Gdansk Long Market Walk",
                "participant_keys": ("jan-kowalski",),
                "starts_at": now - timedelta(days=11, hours=11),
                "ends_at": now - timedelta(days=11, hours=15),
                "score": 4,
                "reviewed_at": now - timedelta(days=10, hours=17),
                "info": (
                    "Compact and pleasant city walk. Long Market is an excellent start, and "
                    "the route toward Westerplatte adds historical depth."
                ),
            },
            {
                "route_name": "Gdansk Long Market Walk",
                "participant_keys": ("alice-martin", "jan-kowalski"),
                "starts_at": now - timedelta(days=4, hours=10),
                "ends_at": now - timedelta(days=4, hours=14),
                "score": 5,
                "reviewed_at": now - timedelta(days=3, hours=18),
                "info": (
                    "Excellent urban route. The shift from city center atmosphere to "
                    "Westerplatte's historic landscape is very effective."
                ),
            },
            {
                "route_name": "Gdansk Long Market Walk",
                "participant_keys": ("jan-kowalski",),
                "starts_at": now + timedelta(days=18, hours=11),
                "ends_at": now + timedelta(days=18, hours=15),
                "score": None,
                "reviewed_at": None,
                "info": "A planned city walk from Long Market toward Westerplatte.",
            },
        ]

        for travel in travel_data:
            self.upsert_travel(**travel)

    def create_visits(self):
        now = timezone.now()

        visit_data = [
            {
                "participant_key": "alice-martin",
                "location_key": "eiffel_tower",
                "score": 5,
                "visited_at": now - timedelta(days=25, hours=11),
                "reviewed_at": now - timedelta(days=24, hours=18),
                "review": (
                    "Iconic landmark with an excellent view over Paris. "
                    "The area was busy, but the visit felt worth it and easy to combine with a longer city walk."
                ),
            },
            {
                "participant_key": "marie-dubois",
                "location_key": "eiffel_tower",
                "score": 4,
                "visited_at": now - timedelta(days=14, hours=10),
                "reviewed_at": now - timedelta(days=13, hours=20),
                "review": "A beautiful stop with strong atmosphere. Best visited earlier in the day before the crowds build up.",
            },
            {
                "participant_key": "emma-smith",
                "location_key": "eiffel_tower",
                "score": 5,
                "visited_at": now - timedelta(days=7, hours=16),
                "reviewed_at": now - timedelta(days=6, hours=9),
                "review": "Very memorable. The surrounding views make it feel like more than just a single monument.",
            },
            {
                "participant_key": "alice-martin",
                "location_key": "notre_dame",
                "score": 4,
                "visited_at": now - timedelta(days=24, hours=13),
                "reviewed_at": now - timedelta(days=23, hours=19),
                "review": (
                    "Beautiful historic site with strong cultural significance. "
                    "The surrounding streets add a lot to the experience."
                ),
            },
            {
                "participant_key": "marie-dubois",
                "location_key": "notre_dame",
                "score": 5,
                "visited_at": now - timedelta(days=15, hours=12),
                "reviewed_at": now - timedelta(days=14, hours=8),
                "review": "A deeply meaningful Paris landmark. Excellent for visitors interested in architecture and history.",
            },
            {
                "participant_key": "marie-dubois",
                "location_key": "louvre",
                "score": 5,
                "visited_at": now - timedelta(days=21, hours=10),
                "reviewed_at": now - timedelta(days=20, hours=17),
                "review": (
                    "Outstanding museum visit with world-class collections. "
                    "It needs more time than expected, so a focused route through the museum helps."
                ),
            },
            {
                "participant_key": "alice-martin",
                "location_key": "louvre",
                "score": 4,
                "visited_at": now - timedelta(days=9, hours=14),
                "reviewed_at": now - timedelta(days=8, hours=12),
                "review": "Excellent collection and location. A little overwhelming without a clear plan.",
            },
            {
                "participant_key": "emma-smith",
                "location_key": "tintagel_castle",
                "score": 4,
                "visited_at": now - timedelta(days=20, hours=9),
                "reviewed_at": now - timedelta(days=19, hours=18),
                "review": (
                    "Dramatic coastal ruins with Arthurian atmosphere. "
                    "The landscape is the strongest part of the visit."
                ),
            },
            {
                "participant_key": "marie-dubois",
                "location_key": "tintagel_castle",
                "score": 5,
                "visited_at": now - timedelta(days=12, hours=11),
                "reviewed_at": now - timedelta(days=11, hours=10),
                "review": "A very evocative site. The cliffs and ruins make the place feel distinctive and memorable.",
            },
            {
                "participant_key": "emma-smith",
                "location_key": "stonehenge",
                "score": 5,
                "visited_at": now - timedelta(days=18, hours=10),
                "reviewed_at": now - timedelta(days=17, hours=13),
                "review": "Memorable prehistoric monument visit. It works especially well as the end point of a longer heritage route.",
            },
            {
                "participant_key": "alice-martin",
                "location_key": "stonehenge",
                "score": 4,
                "visited_at": now - timedelta(days=6, hours=15),
                "reviewed_at": now - timedelta(days=5, hours=16),
                "review": "Impressive and atmospheric, though the experience depends a lot on timing and crowd levels.",
            },
            {
                "participant_key": "jan-kowalski",
                "location_key": "warsaw_royal_castle",
                "score": 5,
                "visited_at": now - timedelta(days=28, hours=12),
                "reviewed_at": now - timedelta(days=27, hours=19),
                "review": (
                    "Excellent historic location in Warsaw Old Town. "
                    "A strong anchor point for understanding the city."
                ),
            },
            {
                "participant_key": "emma-smith",
                "location_key": "warsaw_royal_castle",
                "score": 4,
                "visited_at": now - timedelta(days=13, hours=13),
                "reviewed_at": now - timedelta(days=12, hours=9),
                "review": "Very good visit. The surrounding square gives the castle a strong public setting.",
            },
            {
                "participant_key": "jan-kowalski",
                "location_key": "wawel_castle",
                "score": 5,
                "visited_at": now - timedelta(days=23, hours=10),
                "reviewed_at": now - timedelta(days=22, hours=18),
                "review": (
                    "One of the most important royal sites in Poland. "
                    "The hill, cathedral, and castle together make a complete visit."
                ),
            },
            {
                "participant_key": "alice-martin",
                "location_key": "wawel_castle",
                "score": 5,
                "visited_at": now - timedelta(days=8, hours=11),
                "reviewed_at": now - timedelta(days=7, hours=20),
                "review": "Excellent landmark. It feels both historic and spatially impressive.",
            },
            {
                "participant_key": "jan-kowalski",
                "location_key": "long_market",
                "score": 4,
                "visited_at": now - timedelta(days=17, hours=12),
                "reviewed_at": now - timedelta(days=16, hours=18),
                "review": (
                    "Great urban landmark in central Gdansk. "
                    "Good atmosphere and a natural starting point for a city walk."
                ),
            },
            {
                "participant_key": "marie-dubois",
                "location_key": "long_market",
                "score": 5,
                "visited_at": now - timedelta(days=5, hours=14),
                "reviewed_at": now - timedelta(days=4, hours=9),
                "review": "Beautiful street experience with strong architecture and a lively public feel.",
            },
            {
                "participant_key": "jan-kowalski",
                "location_key": "westerplatte",
                "score": 4,
                "visited_at": now - timedelta(days=4, hours=16),
                "reviewed_at": now - timedelta(days=3, hours=10),
                "review": "Historically important and reflective. Best understood after visiting central Gdansk first.",
            },
            {
                "participant_key": "emma-smith",
                "location_key": "mont_saint_michel",
                "score": 5,
                "visited_at": now - timedelta(days=19, hours=9),
                "reviewed_at": now - timedelta(days=18, hours=15),
                "review": "Exceptional place. The approach to the abbey is part of the experience, not just the destination.",
            },
            {
                "participant_key": "alice-martin",
                "location_key": "omaha_beach",
                "score": 5,
                "visited_at": now - timedelta(days=18, hours=15),
                "reviewed_at": now - timedelta(days=17, hours=9),
                "review": "Powerful and quiet location. It adds emotional weight to the wider Normandy route.",
            },
        ]

        for visit in visit_data:
            self.upsert_visit(**visit)

    def process(self):
        if not self.full:
            return

        self.people = {}
        self.routes = {}
        self.addresses = {}

        self.load_people()
        self.load_routes()
        self.load_addresses()

        self.create_travels()
        self.create_visits()

        print("[Ingress] Demo travels and visits created successfully.")
