from django.contrib.gis.geos import Point, Polygon, MultiLineString
from locations.models import PointFeature, ZoneFeature, PathFeature, Address
from oya.ingress import IngressCommand


class Command(IngressCommand):
    help = "Creates demo geospatial features (points, zones, paths) for testing"

    def create_address(self):
        return Address.objects.create(
            country_name="US",
            state_or_province_name="California",
            locality_name="San Francisco",
            street="123 Business St",
            building="HQ Tower",
            apartment="5A"
        )

    def process(self):
        if not self.full:
            return

        # Create sample points
        p1 = PointFeature.objects.create(
            name="Central Park",
            geometry=Point(-73.9654, 40.7829),
        )
        p2 = PointFeature.objects.create(
            name="Eiffel Tower",
            geometry=Point(2.2945, 48.8584),
        )

        # Create sample zones (polygons)
        zone = ZoneFeature.objects.create(
            name="Sample Zone",
            geometry=Polygon((
                (2.29, 48.85),
                (2.30, 48.85),
                (2.30, 48.86),
                (2.29, 48.86),
                (2.29, 48.85),
            )),
        )

        # # Create sample paths (multi-line strings)
        # path = PathFeature.objects.create(
        #     name="River Path",
        #     geometry=MultiLineString(
        #         [
        #             [(2.29, 48.85), (2.295, 48.852), (2.30, 48.855)],
        #             [(2.30, 48.855), (2.305, 48.857), (2.31, 48.86)],
        #         ],
        #     ),
        # )

        address = self.create_address()
        if not address:
            self.stderr.write(self.style.ERROR("❌ Address creation failed."))
            return

        print("[Ingress] Demo geospatial features created successfully.")
