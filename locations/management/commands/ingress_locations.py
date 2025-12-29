from django.contrib.gis.geos import Point, Polygon, MultiLineString
from django.urls import reverse

from locations.models import (
    Territory,
    Route,
    Address,
    Province
)

from oya.ingress import IngressCommand


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
        self.create_dashboard_item(
            title="Locations",
            icon="fa-solid fa-map-location-dot",
            description="Interactive map with points, territories, routes, and addresses.",
            link=reverse("locations:locations_all"),
            public=False,
        )

        if not self.full:
            return

        # Create a sample province
        province = Province.objects.create(
            name="Sample Province",
            capital=self.create_address(),
        )

        # Create sample territory (polygon)
        Territory.objects.create(
            name="Sample Territory",
            province=province,
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
