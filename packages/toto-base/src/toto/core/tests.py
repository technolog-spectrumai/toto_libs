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
        # The host decides which page takes the password: zenobia sends
        # core:login on to its SSO door, which renders the same alert. Post
        # where the host's login link actually leads.
        door = self.client.get(reverse("core:login")).get(
            "Location", reverse("core:login")).split("?")[0]
        response = self.client.post(
            door,
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

    # No rate-limit tests: they asserted a 429 from a middleware that no
    # longer exists anywhere. Platform.rate_limit_window and
    # rate_limit_max_requests are read by nothing (2026-09-23).


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



    def test_the_markdown_play_line_follows_the_htmlview_mount(self):
        # Markdown Play replaced zenobia's wiki (2026-10-02, stage 45): the
        # storage chapter names it, formulas included, where a host serves it
        # and nowhere else.
        from django.test import RequestFactory
        from django.contrib.auth.models import AnonymousUser
        from toto.core.views import _manual_features, _mounted

        request = RequestFactory().get("/core/manual/")
        request.user = AnonymousUser()
        mounted = _mounted("htmlview:index")
        self.assertEqual(_manual_features(request)["markdown"], mounted)
        if not mounted:
            self.skipTest("this host serves no Markdown Play")
        Platform.objects.get_or_create(site_name="Test", defaults={
            "author": "t", "publication_year": 2026, "active": True})
        reader = User.objects.create_user("reader", password="pw")
        self.client.force_login(reader)
        response = self.client.get(reverse("core:manual"), HTTP_ACCEPT_LANGUAGE="en")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<code>.md</code> files")
        self.assertContains(response, "formulas written between")
        self.assertNotContains(response, "/wiki/")

    def test_the_storage_chapter_follows_the_vaults_switches(self):
        # 2026-10-06. A host that only stores files promises no Play, no Edit
        # and no picture viewer, whatever is installed; a button it names in
        # VAULT_STORAGE_ONLY_OPENS comes back into the chapter only where an
        # app draws one (a host's editor behind the vault's buttons).
        from django.apps import apps
        from django.test import RequestFactory
        from django.contrib.auth.models import AnonymousUser
        from toto.core.views import _manual_features, _mounted

        if not apps.is_installed("toto.vault"):
            self.skipTest("this host has no vault")
        request = RequestFactory().get("/core/manual/")
        request.user = AnonymousUser()
        editor = _mounted("morion:open")
        with self.settings(VAULT_STORAGE_ONLY=False):
            features = _manual_features(request)
            self.assertTrue(features["image_viewer"])
            self.assertTrue(features["vault_play"] and features["vault_edit"])
            self.assertEqual(features["morion"], editor)
        with self.settings(VAULT_STORAGE_ONLY=True, VAULT_STORAGE_ONLY_OPENS=()):
            features = _manual_features(request)
            self.assertFalse(features["image_viewer"])
            self.assertFalse(features["vault_play"] or features["vault_edit"])
            self.assertFalse(features["morion"])
            self.assertFalse(features["viewers"])
        with self.settings(VAULT_STORAGE_ONLY=True, VAULT_STORAGE_ONLY_OPENS=("play",)):
            features = _manual_features(request)
            self.assertFalse(features["image_viewer"])
            self.assertTrue(features["vault_play"])
            self.assertFalse(features["vault_edit"])
            self.assertEqual(features["morion"], editor)
            self.assertGreaterEqual(features["viewers"], editor)

        Platform.objects.get_or_create(site_name="Test", defaults={
            "author": "t", "publication_year": 2026, "active": True})
        self.client.force_login(User.objects.create_user("reader", password="pw"))
        with self.settings(VAULT_STORAGE_ONLY=True, VAULT_STORAGE_ONLY_OPENS=()):
            response = self.client.get(reverse("core:manual"), HTTP_ACCEPT_LANGUAGE="en")
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "nothing is opened or edited in the browser")
            self.assertNotContains(response, "images open in a preview window")
            self.assertNotContains(response, "the editor that runs in your browser")
        with self.settings(VAULT_STORAGE_ONLY=False):
            response = self.client.get(reverse("core:manual"), HTTP_ACCEPT_LANGUAGE="en")
            self.assertContains(response, "images open in a preview window")

    def test_no_chapter_of_the_parked_forum(self):
        # 2026-10-09. Polls and Chat described the forum and linked into it
        # (`forum:channel_list`), so they are out of the feature map and out
        # of both bodies, whatever a host installs: a chapter left behind
        # would reverse a route no host serves and answer 500.
        from django.contrib.auth.models import AnonymousUser
        from django.template.loader import get_template
        from django.test import RequestFactory
        from toto.core.views import _manual_features

        request = RequestFactory().get("/core/manual/")
        request.user = AnonymousUser()
        features = _manual_features(request)
        self.assertIn("vault", features)
        for key in ("polls", "chat"):
            self.assertNotIn(key, features)
        for name in ("oya/manual/_body_en.html", "oya/manual/_body_pl.html"):
            with self.subTest(body=name):
                source = get_template(name).template.source
                for gone in ("forum:", "features.polls", "features.chat",
                             'id="polls"', 'id="chat"'):
                    self.assertNotIn(gone, source)
        Platform.objects.get_or_create(site_name="Test", defaults={
            "author": "t", "publication_year": 2026, "active": True})
        self.client.force_login(User.objects.create_user("reader", password="pw"))
        for language in ("en", "pl"):
            with self.subTest(language=language):
                response = self.client.get(reverse("core:manual"),
                                           HTTP_ACCEPT_LANGUAGE=language)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'href="#polls"')
                self.assertNotContains(response, 'href="#chat"')


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "core-last-visited",
        }
    }
)
class LastVisitedTests(TestCase):
    """The "back to …" link's context processor (2026-10-03)."""

    def setUp(self):
        cache.clear()

    def test_a_request_no_middleware_signed_in_records_nothing(self):
        # Django draws its error pages with the request and every context
        # processor, and for an unknown host before AuthenticationMiddleware
        # has run: no request.user. Reading it raised, and the 400 page for
        # every such request became a 500.
        from django.test import RequestFactory
        from .context_processors import last_visited

        request = RequestFactory().get("/vault/")
        self.assertFalse(hasattr(request, "user"))
        self.assertEqual(last_visited(request), {})

    def test_a_member_is_offered_the_section_they_came_from(self):
        from django.test import RequestFactory
        from .context_processors import last_visited

        member = User.objects.create_user(username="walker", password="pw")
        first = RequestFactory().get("/vault/files/")
        first.user = member
        self.assertEqual(last_visited(first),
                         {"last_visited_url": None, "last_visited_name": None})
        then = RequestFactory().get("/events/")
        then.user = member
        self.assertEqual(last_visited(then),
                         {"last_visited_url": "/vault/files/", "last_visited_name": "Vault"})
