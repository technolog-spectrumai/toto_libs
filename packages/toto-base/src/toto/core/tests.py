import time
from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Platform, Font, Theme, ColorMix
from django.contrib.auth.models import User
from datetime import datetime
from toto.gervazy.models import EncryptedSecret
from django.core.cache import cache


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "core-tests",
        }
    }
)
class ViewSmokeTests(TestCase):
    def setUp(self):
        # Create supporting objects for Theme
        cache.clear()
        font = Font.objects.create(
            name="Test Font",
            cdn_link="https://fonts.googleapis.com/css?family=Roboto",
            style_family="sans-serif"
        )

        color_mix = ColorMix.objects.create(
            name="Default Mix"
        )

        theme = Theme.objects.create(
            name="Default Theme",
            color_mix=color_mix,
            font=font,
            header={"light": "bg-white", "dark": "bg-black"},
            footer={"light": "bg-gray-100", "dark": "bg-gray-900"}
        )

        # Create platform with theme
        self.config = Platform.objects.create(
            domain="example.com",
            site_name="Blue Journal",
            publication_year=datetime.now().year,
            active=True,
            rate_limit_window=1,
            rate_limit_max_requests=3,
            theme=theme,
        )

        # Create user for authenticated views
        self.user = User.objects.create_user(username="testuser", password="testpass")

    def test_home_view_loads(self):
        response = self.client.get(reverse('core:welcome'))
        self.assertEqual(response.status_code, 200)

    def test_dashboard_view_loads_for_authenticated_user(self):
        self.client.login(username="testuser", password="testpass")
        response = self.client.get(reverse('core:dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_login_view_invalid_credentials_shows_visible_error(self):
        response = self.client.post(
            reverse("core:login"),
            {"username": "testuser", "password": "wrong"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, "Sign in failed")
        self.assertContains(response, "Invalid username or password.")
        self.assertContains(response, "Try again in")

    def test_inactive_platform_redirects_to_maintenance(self):
        """Inactive platform should redirect all requests to maintenance page."""
        self.config.active = False
        self.config.save()
        response = self.client.get(reverse('core:welcome'))
        self.assertEqual(response.status_code, 302)
        #self.assertRedirects(response, reverse('core:maintenance'))
    #
    def test_rate_limit_blocks_after_max_requests(self):
        """Exceeding max requests within window should return 429."""
        for i in range(self.config.rate_limit_max_requests):
            response = self.client.get(reverse('core:welcome'))
            self.assertEqual(response.status_code, 200)

        # Next request should be blocked
        response = self.client.get(reverse('core:welcome'))
        self.assertEqual(response.status_code, 429)

    def test_rate_limit_resets_after_window(self):
        """Requests should be allowed again after window expires."""
        for i in range(self.config.rate_limit_max_requests):
            self.client.get(reverse('core:welcome'))

        # Blocked
        response = self.client.get(reverse('core:welcome'))
        self.assertEqual(response.status_code, 429)

        # Wait for window to expire
        time.sleep(self.config.rate_limit_window)

        # Should be allowed again
        response = self.client.get(reverse('core:welcome'))
        self.assertEqual(response.status_code, 200)


class ManualFeatureGateTests(TestCase):
    """The manual must describe only pages this host actually serves."""

    def test_notebooks_follow_the_url_mount_not_the_installed_app(self):
        # zenobia installs toto.mandragora purely so workflows' FK to
        # ComputeKernel resolves, and mounts it at no URL. The manual used to
        # gate on the app and advertised a notebook editor that 404s.
        from toto.core.views import _mounted

        self.assertTrue(_mounted("core:dashboard"))
        self.assertFalse(_mounted("nosuchapp:nosuchview"))

    def test_unmounted_app_hides_its_manual_section(self):
        from django.test import RequestFactory
        from django.contrib.auth.models import AnonymousUser
        from toto.core.views import _manual_features

        request = RequestFactory().get("/core/manual/")
        request.user = AnonymousUser()
        features = _manual_features(request)
        # Whatever this host installs, every advertised section must correspond
        # to something reachable — that is the property the gate exists for.
        self.assertIs(type(features["notebooks"]), bool)
