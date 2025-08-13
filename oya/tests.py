from django.test import TestCase
from django.urls import reverse
from oya.models import PlatformConfig, Address, TLSCertificate
from django.contrib.auth.models import User
from datetime import datetime

class ViewSmokeTests(TestCase):
    def setUp(self):
        # Create address
        self.address = Address.objects.create(
            country_name="US",
            state_or_province_name="California",
            locality_name="San Francisco",
            street="123 Business St",
            building="HQ Tower"
        )

        # Create platform config
        self.config = PlatformConfig.objects.create(
            domain="example.com",
            site_name="Sicilian Blue Journal",
            company_name="Blue Media Inc.",
            publication_year=datetime.now().year,
            active=True,
            address=self.address
        )

        # Create user for authenticated views
        self.user = User.objects.create_user(username="testuser", password="testpass")

    def test_home_view_loads(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)

    def test_dashboard_view_loads_for_authenticated_user(self):
        self.client.login(username="testuser", password="testpass")
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
