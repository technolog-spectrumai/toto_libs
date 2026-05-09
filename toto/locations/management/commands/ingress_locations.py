from django.contrib.gis.geos import LineString, MultiLineString, MultiPolygon, Point, Polygon

from toto.core.ingress import IngressCommand
from toto.locations.models import Address, Route, Territory, Zone


class Command(IngressCommand):
    help = "Creates demo geospatial features for France, England, and Poland."

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
        Route.objects.update_or_create(
            name=name,
            defaults={
                "geometry": self.route_geometry(coordinates),
                "start_address": self.addresses[start_key],
                "end_address": self.addresses[end_key],
            },
        )

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

    def process(self):
        if not self.full:
            return

        self.addresses = {}
        self.territories = {}

        self.create_addresses()
        self.create_territories()
        self.create_zones()
        self.create_routes()

        print("[Ingress] Demo European geospatial features created successfully.")
