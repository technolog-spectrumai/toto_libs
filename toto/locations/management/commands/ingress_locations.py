from django.contrib.gis.geos import Point, Polygon, MultiLineString
from toto.locations.models import (
    Territory,
    Route,
    Address
)
from toto.core.ingress import IngressCommand


class Command(IngressCommand):
    help = "Creates demo geospatial features (points, territories, routes, and addresses) for testing"

    def create_address(self):
        return Address.objects.create(
            country_name="US",
            state_or_province_name="California",
            locality_name="San Francisco",
            street="123 Business St",
            building="HQ Tower",
            apartment="5A",
            geometry=Point(-122.4194, 37.7749)
        )

    def process(self):

        if not self.full:
            return

        # Create sample territory (polygon)
        Territory.objects.create(
            name="Sample Territory",
            geometry=Polygon((
                (2.29, 48.85),
                (2.30, 48.85),
                (2.30, 48.86),
                (2.29, 48.86),
                (2.29, 48.85),
            )),
            capital=self.create_address(),
        )

        print("[Ingress] Demo geospatial features created successfully.")
