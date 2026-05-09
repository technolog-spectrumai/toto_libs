from datetime import timedelta

from django.contrib.gis.geos import LineString, MultiLineString, MultiPolygon, Point, Polygon
from django.utils import timezone

from toto.core.ingress import IngressCommand
from toto.locations.models import (
    Address,
    MapLayer,
    MapLayerPolygon,
    Route,
    RouteChain,
    Territory,
    Travel,
    Visit,
    Zone,
)
from toto.socialhub.models import Person


class Command(IngressCommand):
    help = "Creates demo geospatial features, map layers with owners, travels, and visits for France, England, and Poland."

    def point(self, longitude, latitude):
        geometry = Point(longitude, latitude)
        geometry.srid = 4326
        return geometry

    def polygon(self, coordinates):
        geometry = Polygon(tuple(coordinates))
        geometry.srid = 4326
        return geometry

    def multipolygon(self, *rings):
        geometry = MultiPolygon(*[self.polygon(ring) for ring in rings])
        geometry.srid = 4326
        return geometry

    def route_geometry(self, coordinates):
        line = LineString(*coordinates)
        geometry = MultiLineString(line)
        geometry.srid = 4326
        return geometry

    def upsert_person(self, key, display_name, email=None, phone=None):
        person = (
                Person.objects.filter(slug=key).first()
                or (Person.objects.filter(email=email).first() if email else None)
                or Person.objects.filter(display_name=display_name).first()
        )

        if not person:
            person = Person.objects.create(
                slug=key,
                display_name=display_name,
                email=email,
                phone=phone,
            )
        else:
            changed_fields = []

            if not person.slug:
                person.slug = key
                changed_fields.append("slug")

            if not person.display_name:
                person.display_name = display_name
                changed_fields.append("display_name")

            if email and not person.email:
                person.email = email
                changed_fields.append("email")

            if phone and not person.phone:
                person.phone = phone
                changed_fields.append("phone")

            if changed_fields:
                person.save(update_fields=changed_fields)

        self.people[key] = person
        return person

    def upsert_address(self, key, country, region, locality, place, longitude, latitude, building="Landmark"):
        address, _ = Address.objects.update_or_create(
            country_name=country,
            state_or_province_name=region,
            locality_name=locality,
            street=place,
            building=building,
            defaults={
                "apartment": "",
                "geometry": self.point(longitude, latitude),
            },
        )
        self.addresses[key] = address
        return address

    def upsert_territory(self, name, coordinates, capital_key=None):
        territory, _ = Territory.objects.update_or_create(
            name=name,
            defaults={
                "geometry": self.polygon(coordinates),
                "capital": self.addresses.get(capital_key),
            },
        )
        self.territories[name] = territory
        return territory

    def upsert_zone(self, name, territory_name, *rings):
        Zone.objects.update_or_create(
            name=name,
            defaults={
                "geometry": self.multipolygon(*rings),
                "territory": self.territories.get(territory_name),
            },
        )

    def upsert_route(self, name, start_key, end_key, coordinates):
        route, _ = Route.objects.update_or_create(
            name=name,
            defaults={
                "geometry": self.route_geometry(coordinates),
                "start_address": self.addresses[start_key],
                "end_address": self.addresses[end_key],
            },
        )
        self.routes[name] = route
        return route

    def upsert_route_chain(self, name, description, route_names):
        route_chain, _ = RouteChain.objects.update_or_create(
            name=name,
            defaults={
                "description": description,
            },
        )

        for sequence, route_name in enumerate(route_names, start=1):
            Route.objects.filter(name=route_name).update(
                route_chain=route_chain,
                sequence=sequence,
            )

        return route_chain

    def upsert_travel(self, route_name, participant_keys, starts_at, ends_at, info=""):
        travel, _ = Travel.objects.update_or_create(
            route=self.routes[route_name],
            starts_at=starts_at,
            defaults={
                "ends_at": ends_at,
                "info": info,
            },
        )

        travel.participants.set(
            [self.people[key] for key in participant_keys]
        )

        self.travels[route_name] = travel
        return travel

    def upsert_visit(self, participant_key, location_key, score=None, review=""):
        visit, _ = Visit.objects.update_or_create(
            participant=self.people[participant_key],
            location=self.addresses[location_key],
            defaults={
                "score": score,
                "review": review,
            },
        )
        return visit

    def upsert_map_layer(
        self,
        name,
        slug,
        description,
        unit,
        style,
        inverted_importance=False,
        half_range=False,
        min_value=None,
        max_value=None,
        owner_key=None,
    ):
        layer, _ = MapLayer.objects.update_or_create(
            slug=slug,
            defaults={
                "name": name,
                "description": description,
                "unit": unit,
                "min_value": min_value,
                "max_value": max_value,
                "style": style,
                "inverted_importance": inverted_importance,
                "half_range": half_range,
                "is_active": True,
                "owner": self.people.get(owner_key) if owner_key else None,
            },
        )
        self.map_layers[slug] = layer
        return layer

    def polygon_center(self, coordinates):
        longitudes = [longitude for longitude, latitude in coordinates[:-1]]
        latitudes = [latitude for longitude, latitude in coordinates[:-1]]
        return self.point(
            sum(longitudes) / len(longitudes),
            sum(latitudes) / len(latitudes),
        )

    def upsert_map_layer_polygon(self, layer_slug, name, value, coordinates, properties=None):
        MapLayerPolygon.objects.update_or_create(
            layer=self.map_layers[layer_slug],
            name=name,
            defaults={
                "geometry": self.polygon(coordinates),
                "center": self.polygon_center(coordinates),
                "value": value,
                "properties": properties or {},
            },
        )

    def create_people(self):
        owner_keys = [
            "alice-martin",
            "jan-kowalski",
            "emma-smith",
            "marie-dubois",
        ]

        fallback_people = [
            ("alice-martin", "Alice Martin", "alice@example.com", "+33111111111"),
            ("jan-kowalski", "Jan Kowalski", "jan@example.com", "+48222222222"),
            ("emma-smith", "Emma Smith", "emma@example.com", "+44333333333"),
            ("marie-dubois", "Marie Dubois", "marie@example.com", "+33444444444"),
        ]

        existing_people = list(
            Person.objects
            .exclude(display_name__isnull=True)
            .exclude(display_name="")
            .order_by("id")[:len(owner_keys)]
        )

        for key, person in zip(owner_keys, existing_people):
            self.people[key] = person

        missing_count = len(owner_keys) - len(existing_people)

        if missing_count <= 0:
            return

        for fallback in fallback_people[len(existing_people):]:
            self.upsert_person(*fallback)

    def create_addresses(self):
        address_data = [
            ("eiffel_tower", "FR", "Ile-de-France", "Paris", "Eiffel Tower", 2.2945, 48.8584),
            ("louvre", "FR", "Ile-de-France", "Paris", "Louvre Museum", 2.3364, 48.8606),
            ("notre_dame", "FR", "Ile-de-France", "Paris", "Notre-Dame Cathedral", 2.3499, 48.8530),
            ("mont_saint_michel", "FR", "Normandy", "Le Mont-Saint-Michel", "Mont Saint-Michel Abbey", -1.5115, 48.6361),
            ("omaha_beach", "FR", "Normandy", "Colleville-sur-Mer", "Omaha Beach", -0.8710, 49.3733),
            ("rouen_cathedral", "FR", "Normandy", "Rouen", "Rouen Cathedral", 1.0949, 49.4405),
            ("saint_malo", "FR", "Brittany", "Saint-Malo", "Saint-Malo Intra-Muros", -2.0257, 48.6493),
            ("carnac_stones", "FR", "Brittany", "Carnac", "Carnac Stones", -3.0789, 47.5929),
            ("avignon_palace", "FR", "Provence-Alpes-Cote d'Azur", "Avignon", "Palais des Papes", 4.8075, 43.9509),
            ("aix_mirabeau", "FR", "Provence-Alpes-Cote d'Azur", "Aix-en-Provence", "Cours Mirabeau", 5.4487, 43.5263),
            ("nice_promenade", "FR", "Provence-Alpes-Cote d'Azur", "Nice", "Promenade des Anglais", 7.2653, 43.6951),
            ("cannes_croisette", "FR", "Provence-Alpes-Cote d'Azur", "Cannes", "Boulevard de la Croisette", 7.0260, 43.5506),
            ("tintagel_castle", "GB", "England", "Tintagel", "Tintagel Castle", -4.7599, 50.6670),
            ("stonehenge", "GB", "England", "Amesbury", "Stonehenge", -1.8262, 51.1789),
            ("tower_of_london", "GB", "England", "London", "Tower of London", -0.0761, 51.5081),
            ("warsaw_royal_castle", "PL", "Masovian", "Warsaw", "Royal Castle", 21.0156, 52.2477),
            ("warsaw_palace_culture", "PL", "Masovian", "Warsaw", "Palace of Culture and Science", 21.0058, 52.2318),
            ("wawel_castle", "PL", "Lesser Poland", "Krakow", "Wawel Castle", 19.9352, 50.0540),
            ("krakow_main_square", "PL", "Lesser Poland", "Krakow", "Main Market Square", 19.9373, 50.0619),
            ("long_market", "PL", "Pomeranian", "Gdansk", "Long Market", 18.6538, 54.3487),
            ("neptune_fountain", "PL", "Pomeranian", "Gdansk", "Neptune Fountain", 18.6539, 54.3486),
            ("westerplatte", "PL", "Pomeranian", "Gdansk", "Westerplatte", 18.6717, 54.4067),
        ]

        for address in address_data:
            self.upsert_address(*address)

    def create_territories(self):
        self.upsert_territory(
            "Paris Core",
            ((2.2241, 48.8156), (2.4699, 48.8156), (2.4699, 48.9022), (2.2241, 48.9022), (2.2241, 48.8156)),
            "notre_dame",
        )
        self.upsert_territory(
            "Normandy Coast",
            ((-1.95, 48.55), (1.80, 48.55), (1.80, 49.80), (-1.95, 49.80), (-1.95, 48.55)),
            "mont_saint_michel",
        )
        self.upsert_territory(
            "Brittany",
            ((-5.20, 47.25), (-1.00, 47.25), (-1.00, 48.95), (-5.20, 48.95), (-5.20, 47.25)),
            "saint_malo",
        )
        self.upsert_territory(
            "Provence",
            ((4.15, 43.20), (6.15, 43.20), (6.15, 44.25), (4.15, 44.25), (4.15, 43.20)),
            "avignon_palace",
        )
        self.upsert_territory(
            "Cote d'Azur",
            ((6.10, 43.35), (7.75, 43.35), (7.75, 44.05), (6.10, 44.05), (6.10, 43.35)),
            "nice_promenade",
        )
        self.upsert_territory(
            "Arthurian England",
            ((-5.00, 50.40), (-0.05, 50.40), (-0.05, 51.75), (-5.00, 51.75), (-5.00, 50.40)),
            "tintagel_castle",
        )
        self.upsert_territory(
            "Polish Royal Cities",
            ((18.20, 49.85), (22.20, 49.85), (22.20, 54.65), (18.20, 54.65), (18.20, 49.85)),
            "warsaw_royal_castle",
        )

    def create_zones(self):
        self.upsert_zone(
            "Paris Landmarks Zone",
            "Paris Core",
            ((2.2850, 48.8500), (2.3600, 48.8500), (2.3600, 48.8700), (2.2850, 48.8700), (2.2850, 48.8500)),
        )
        self.upsert_zone(
            "D-Day Memory Zone",
            "Normandy Coast",
            ((-1.10, 49.25), (-0.20, 49.25), (-0.20, 49.50), (-1.10, 49.50), (-1.10, 49.25)),
        )
        self.upsert_zone(
            "Breton Heritage Zone",
            "Brittany",
            ((-3.25, 47.50), (-1.85, 47.50), (-1.85, 48.75), (-3.25, 48.75), (-3.25, 47.50)),
        )
        self.upsert_zone(
            "Avignon and Aix Zone",
            "Provence",
            ((4.70, 43.45), (5.55, 43.45), (5.55, 44.05), (4.70, 44.05), (4.70, 43.45)),
        )
        self.upsert_zone(
            "Riviera Promenade Zone",
            "Cote d'Azur",
            ((6.90, 43.48), (7.35, 43.48), (7.35, 43.78), (6.90, 43.78), (6.90, 43.48)),
        )
        self.upsert_zone(
            "Tintagel Legend Zone",
            "Arthurian England",
            ((-4.90, 50.58), (-4.55, 50.58), (-4.55, 50.78), (-4.90, 50.78), (-4.90, 50.58)),
        )
        self.upsert_zone(
            "Warsaw Old Town Zone",
            "Polish Royal Cities",
            ((20.98, 52.22), (21.04, 52.22), (21.04, 52.27), (20.98, 52.27), (20.98, 52.22)),
        )
        self.upsert_zone(
            "Krakow Royal Route Zone",
            "Polish Royal Cities",
            ((19.91, 50.045), (19.955, 50.045), (19.955, 50.070), (19.91, 50.070), (19.91, 50.045)),
        )
        self.upsert_zone(
            "Gdansk Main Town Zone",
            "Polish Royal Cities",
            ((18.635, 54.342), (18.665, 54.342), (18.665, 54.358), (18.635, 54.358), (18.635, 54.342)),
        )

    def create_routes(self):
        self.upsert_route(
            "Paris Monument Walk",
            "eiffel_tower",
            "notre_dame",
            ((2.2945, 48.8584), (2.3200, 48.8600), (2.3364, 48.8606), (2.3499, 48.8530)),
        )
        self.upsert_route(
            "Normandy Heritage Line",
            "mont_saint_michel",
            "omaha_beach",
            ((-1.5115, 48.6361), (-1.2500, 49.0000), (-0.8710, 49.3733)),
        )
        self.upsert_route(
            "Brittany Coast and Stones",
            "saint_malo",
            "carnac_stones",
            ((-2.0257, 48.6493), (-2.5000, 48.1000), (-3.0789, 47.5929)),
        )
        self.upsert_route(
            "Provence to Riviera",
            "avignon_palace",
            "nice_promenade",
            ((4.8075, 43.9509), (5.4487, 43.5263), (7.0260, 43.5506), (7.2653, 43.6951)),
        )
        self.upsert_route(
            "Arthurian South England",
            "tintagel_castle",
            "stonehenge",
            ((-4.7599, 50.6670), (-3.2000, 50.9000), (-1.8262, 51.1789)),
        )
        self.upsert_route(
            "Polish Royal Trail",
            "warsaw_royal_castle",
            "wawel_castle",
            ((21.0156, 52.2477), (20.1000, 51.5000), (19.9352, 50.0540)),
        )
        self.upsert_route(
            "Gdansk Long Market Walk",
            "long_market",
            "westerplatte",
            ((18.6538, 54.3487), (18.6539, 54.3486), (18.6600, 54.3700), (18.6717, 54.4067)),
        )

    def create_route_chains(self):
        self.upsert_route_chain(
            "Northern France Heritage Chain",
            "Paris, Normandy, and Brittany landmarks connected as a broad northern France route chain.",
            (
                "Paris Monument Walk",
                "Normandy Heritage Line",
                "Brittany Coast and Stones",
            ),
        )
        self.upsert_route_chain(
            "Southern France Sun Chain",
            "A Provence and Cote d'Azur chain from papal Avignon through Cannes to Nice.",
            (
                "Provence to Riviera",
            ),
        )
        self.upsert_route_chain(
            "Arthurian England Chain",
            "A south England route from Tintagel Castle toward Stonehenge.",
            (
                "Arthurian South England",
            ),
        )
        self.upsert_route_chain(
            "Polish Landmark Chain",
            "A Polish route chain covering Warsaw, Krakow, and the Gdansk Long Market area.",
            (
                "Polish Royal Trail",
                "Gdansk Long Market Walk",
            ),
        )

    def create_travels(self):
        now = timezone.now()

        self.upsert_travel(
            "Paris Monument Walk",
            ("alice-martin", "marie-dubois"),
            now + timedelta(days=3, hours=10),
            now + timedelta(days=3, hours=14),
            "A guided walk through major Paris landmarks.",
        )
        self.upsert_travel(
            "Normandy Heritage Line",
            ("alice-martin", "emma-smith"),
            now + timedelta(days=7, hours=9),
            now + timedelta(days=7, hours=18),
            "A heritage route from Mont Saint-Michel toward Omaha Beach.",
        )
        self.upsert_travel(
            "Arthurian South England",
            ("emma-smith",),
            now + timedelta(days=12, hours=8),
            now + timedelta(days=12, hours=17),
            "A mythic route from Tintagel Castle toward Stonehenge.",
        )
        self.upsert_travel(
            "Polish Royal Trail",
            ("jan-kowalski", "alice-martin"),
            now + timedelta(days=15, hours=9),
            now + timedelta(days=15, hours=20),
            "A royal landmark route between Warsaw and Krakow.",
        )
        self.upsert_travel(
            "Gdansk Long Market Walk",
            ("jan-kowalski",),
            now + timedelta(days=18, hours=11),
            now + timedelta(days=18, hours=15),
            "A city walk from Long Market toward Westerplatte.",
        )

    def create_visits(self):
        visit_data = [
            ("alice-martin", "eiffel_tower", 5, "Iconic landmark with an excellent view over Paris."),
            ("alice-martin", "notre_dame", 4, "Beautiful historic site with strong cultural significance."),
            ("marie-dubois", "louvre", 5, "Outstanding museum visit with world-class collections."),
            ("emma-smith", "tintagel_castle", 4, "Dramatic coastal ruins with Arthurian atmosphere."),
            ("emma-smith", "stonehenge", 5, "Memorable prehistoric monument visit."),
            ("jan-kowalski", "warsaw_royal_castle", 5, "Excellent historic location in Warsaw Old Town."),
            ("jan-kowalski", "wawel_castle", 5, "One of the most important royal sites in Poland."),
            ("jan-kowalski", "long_market", 4, "Great urban landmark in central Gdansk."),
        ]

        for visit in visit_data:
            self.upsert_visit(*visit)

    def create_map_layers(self):
        self.upsert_map_layer(
            "Tourist Intensity",
            "tourist-intensity",
            "Approximate tourist pressure in the current demo regions.",
            "%",
            {
                "colors": ["#4a8f7a", "#d94a4a"],
                "palette": "heat",
                "opacity": 0.32,
                "show_labels": True,
            },
            min_value=20,
            max_value=95,
            owner_key="alice-martin",
        )
        self.upsert_map_layer(
            "Summer Heat",
            "summer-heat",
            "Indicative warm-season comfort layer for travel planning.",
            "°C",
            {
                "colors": ["#5f7fc9", "#ff4455"],
                "palette": "cool",
                "opacity": 0.28,
                "show_labels": True,
            },
            owner_key="marie-dubois",
        )
        self.upsert_map_layer(
            "Precipitation",
            "precipitation",
            "Approximate rainfall layer for testing weather-style overlays.",
            "mm",
            {
                "opacity": 0.34,
                "show_labels": True,
            },
            inverted_importance=True,
            half_range=True,
            min_value=20,
            max_value=95,
            owner_key="jan-kowalski",
        )

        regions = [
            ("Paris", ((2.2241, 48.8156), (2.4699, 48.8156), (2.4699, 48.9022), (2.2241, 48.9022), (2.2241, 48.8156)), 90, 27, 52),
            ("Normandy", ((-1.95, 48.55), (1.80, 48.55), (1.80, 49.80), (-1.95, 49.80), (-1.95, 48.55)), 62, 20, 88),
            ("Brittany", ((-5.20, 47.25), (-1.00, 47.25), (-1.00, 48.95), (-5.20, 48.95), (-5.20, 47.25)), 54, 19, 92),
            ("Provence", ((4.15, 43.20), (6.15, 43.20), (6.15, 44.25), (4.15, 44.25), (4.15, 43.20)), 78, 31, 28),
            ("Cote d'Azur", ((6.10, 43.35), (7.75, 43.35), (7.75, 44.05), (6.10, 44.05), (6.10, 43.35)), 95, 30, 34),
            ("Arthurian England", ((-5.00, 50.40), (-0.05, 50.40), (-0.05, 51.75), (-5.00, 51.75), (-5.00, 50.40)), 48, 18, 78),
            ("Warsaw", ((20.88, 52.12), (21.18, 52.12), (21.18, 52.35), (20.88, 52.35), (20.88, 52.12)), 72, 25, 44),
            ("Krakow", ((19.80, 49.98), (20.08, 49.98), (20.08, 50.12), (19.80, 50.12), (19.80, 49.98)), 84, 26, 58),
            ("Gdansk", ((18.50, 54.29), (18.75, 54.29), (18.75, 54.43), (18.50, 54.43), (18.50, 54.29)), 76, 22, 70),
        ]

        for name, coordinates, tourist_value, heat_value, rain_value in regions:
            self.upsert_map_layer_polygon(
                "tourist-intensity",
                f"{name} tourist intensity",
                tourist_value,
                coordinates,
                {"region": name},
            )
            self.upsert_map_layer_polygon(
                "precipitation",
                f"{name} precipitation",
                rain_value,
                coordinates,
                {"region": name},
            )
            self.upsert_map_layer_polygon(
                "summer-heat",
                f"{name} summer heat",
                heat_value,
                coordinates,
                {"region": name},
            )

    def process(self):
        if not self.full:
            return

        self.addresses = {}
        self.territories = {}
        self.routes = {}
        self.map_layers = {}
        self.people = {}
        self.travels = {}

        self.create_people()

        self.create_addresses()
        self.create_territories()
        self.create_zones()
        self.create_routes()
        self.create_route_chains()
        self.create_map_layers()

        self.create_travels()
        self.create_visits()

        print("[Ingress] Demo European geospatial features, map layer owners, travels, and visits created successfully.")