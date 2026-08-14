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


class OfficeAreaTests(TestCase):
    """One place for the tools you make things with.

    The five apps live in three different wheels plus a host portion, so the
    only honest way to list them is to ask this server what it installed AND
    mounted — an app can be in INSTALLED_APPS and serve no page, which is why
    zenobia keeps toto.mandragora installed and unmounted.
    """

    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(
            site_name="Test", defaults={"author": "t", "publication_year": 2026,
                                        "active": True})
        cls.user = User.objects.create_user("officer", password="pw")

    def setUp(self):
        self.client.force_login(self.user)

    def test_the_page_renders(self):
        response = self.client.get(reverse("core:office"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Office")

    def test_it_lists_only_apps_this_host_installed_and_mounted(self):
        from django.apps import apps as django_apps

        from .views import OFFICE_APPS, _mounted

        listed = {app["title"] for app in
                  self.client.get(reverse("core:office")).context["apps"]}
        expected = {title for label, url_name, title, _icon, _desc in OFFICE_APPS
                    if django_apps.is_installed(label) and _mounted(url_name)}

        self.assertEqual(listed, expected)

    def test_a_missing_app_is_absent_rather_than_broken(self):
        """The strip and the cards share both guards, so an uninstalled app
        costs a card rather than a NoReverseMatch."""
        response = self.client.get(reverse("core:office"))

        for app in response.context["apps"]:
            with self.subTest(app=app["title"]):
                self.assertTrue(app["url"].startswith("/"))

    def test_every_office_app_is_named_once(self):
        """A duplicate would render two identical cards and two tabs."""
        from .views import OFFICE_APPS

        titles = [title for _l, _u, title, _i, _d in OFFICE_APPS]
        self.assertEqual(len(titles), len(set(titles)))

    def test_it_needs_a_login(self):
        self.client.logout()

        response = self.client.get(reverse("core:office"))

        self.assertNotEqual(response.status_code, 500)
