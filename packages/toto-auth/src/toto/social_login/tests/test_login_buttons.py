"""The login page shows a provider's button exactly when it is configured."""
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform


class LoginButtonTests(TestCase):
    def setUp(self):
        # PageProcessor 404s the login page without an active platform.
        Platform.objects.create(site_name="Test Site", author="Test",
                                publication_year=2024, active=True)

    def test_no_buttons_without_credentials(self):
        response = self.client.get(reverse("sso:login"))
        self.assertNotContains(response, "Continue with Google")
        self.assertNotContains(response, "or continue with")

    @override_settings(GOOGLE_OAUTH_CLIENT_ID="cid", GOOGLE_OAUTH_CLIENT_SECRET="sec")
    def test_google_button_when_configured(self):
        response = self.client.get(reverse("sso:login"))
        self.assertContains(response, "Continue with Google")
        self.assertContains(response, reverse("sso:social_login", args=["google"]))
        self.assertNotContains(response, "Continue with Facebook")

    @override_settings(GOOGLE_OAUTH_CLIENT_ID="cid", GOOGLE_OAUTH_CLIENT_SECRET="sec",
                       FACEBOOK_OAUTH_CLIENT_ID="fid", FACEBOOK_OAUTH_CLIENT_SECRET="fsec")
    def test_both_buttons_and_next_propagation(self):
        response = self.client.get(reverse("sso:login"), {"next": "/core/dashboard/"})
        self.assertContains(response, "Continue with Google")
        self.assertContains(response, "Continue with Facebook")
        self.assertContains(response, "next=%2Fcore%2Fdashboard%2F")
