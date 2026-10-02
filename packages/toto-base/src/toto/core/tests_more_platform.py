"""toto.core's always-on machinery (2026-09-29): the language a member's
profile chooses, the maintenance switch, the rate limiter's edges, the
dashboard's visibility arms, whether mail can be delivered, the admin's
batch actions and read-only mode, and the QR transport federation pairing
rides on."""

from importlib.util import find_spec
from types import SimpleNamespace
from unittest import mock, skipUnless

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import translation

from toto.core import email_config, qr, ratelimit
from toto.core.base_admin import TotoModelAdmin
from toto.core.batch import BatchAction, BatchActionResult
from toto.core.middleware import PlatformMiddleware, ProfileLanguageMiddleware
from toto.core.models import Platform
from toto.core.views import _plan_allows, _resolve_dashboard_item
from toto.people.models import Person

User = get_user_model()


class ProfileLanguageTests(TestCase):
    def setUp(self):
        self.addCleanup(translation.deactivate)
        self.ada = User.objects.create_user("ada", password="pw")

    def language_seen(self, user, cookies=None, session=None):
        request = RequestFactory().get("/")
        request.user = user
        request.COOKIES.update(cookies or {})
        request.session = dict(session or {})
        seen = {}

        def view(req):
            seen["active"] = translation.get_language()
            seen["request"] = getattr(req, "LANGUAGE_CODE", None)
            return HttpResponse()

        with translation.override("en"):
            ProfileLanguageMiddleware(view)(request)
        return seen

    def test_a_member_s_profile_language_is_the_page_s_language(self):
        Person.objects.create(user=self.ada, display_name="Ada", preferred_language="pl")
        self.assertEqual(self.language_seen(self.ada), {"active": "pl", "request": "pl"})

    def test_a_language_chosen_for_this_browser_wins_over_the_profile(self):
        from django.conf import settings

        Person.objects.create(user=self.ada, display_name="Ada", preferred_language="pl")
        seen = self.language_seen(self.ada, cookies={settings.LANGUAGE_COOKIE_NAME: "en"})
        self.assertEqual(seen["active"], "en")
        self.assertEqual(self.language_seen(self.ada, session={"_language": "en"})["active"], "en")

    def test_a_language_this_host_does_not_offer_is_ignored(self):
        Person.objects.create(user=self.ada, display_name="Ada", preferred_language="pl")
        Person.objects.filter(user=self.ada).update(preferred_language="xx")
        fresh = User.objects.get(pk=self.ada.pk)        # not the cached profile
        self.assertEqual(self.language_seen(fresh)["active"], "en")

    def test_a_member_without_a_profile_keeps_the_default(self):
        self.assertEqual(self.language_seen(self.ada), {"active": "en", "request": None})

    def test_a_visitor_keeps_the_browser_s_negotiation(self):
        self.assertEqual(self.language_seen(AnonymousUser())["request"], None)


class MaintenanceSwitchTests(TestCase):
    def through(self, path):
        request = RequestFactory().get(path)
        return PlatformMiddleware(lambda r: HttpResponse("page"))(request)

    def test_an_inactive_platform_sends_everyone_to_the_maintenance_page(self):
        Platform.objects.create(site_name="T", author="t", publication_year=2026, active=False)
        response = self.through("/vault/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("core:maintenance"))

    def test_the_admin_and_the_maintenance_page_itself_stay_reachable(self):
        Platform.objects.create(site_name="T", author="t", publication_year=2026, active=False)
        self.assertEqual(self.through("/admin/core/platform/").content, b"page")
        self.assertEqual(self.through(reverse("core:maintenance")).content, b"page")

    def test_an_active_platform_or_none_at_all_lets_the_request_through(self):
        self.assertEqual(self.through("/vault/").content, b"page")
        Platform.objects.create(site_name="T", author="t", publication_year=2026, active=True)
        self.assertEqual(self.through("/vault/").content, b"page")

    def test_a_database_that_is_not_ready_never_blocks_a_request(self):
        with mock.patch.object(Platform.objects, "first", side_effect=RuntimeError("no table")):
            self.assertEqual(self.through("/vault/").content, b"page")


class RateLimitEdgeTests(SimpleTestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def test_the_wait_is_what_is_left_of_the_window(self):
        for _ in range(2):
            result = ratelimit.hit("edge", limit=1, window=60, now=100.0)
        self.assertFalse(result.allowed)
        self.assertEqual((result.remaining, result.retry_after), (0, 20))

    def test_remaining_counts_down_to_zero_and_never_below(self):
        seen = [ratelimit.hit("count", limit=2, window=60, now=0).remaining for _ in range(4)]
        self.assertEqual(seen, [1, 0, 0, 0])

    def test_a_cache_that_answers_nothing_fails_open(self):
        with mock.patch.object(ratelimit.cache, "incr", return_value=None), \
                self.assertLogs("toto.core.ratelimit", "WARNING"):
            result = ratelimit.hit("none", limit=0, window=60)
        self.assertEqual(result, ratelimit.Hit(True, 0, 0))

    def test_a_reset_on_a_broken_cache_is_quiet(self):
        with mock.patch.object(ratelimit.cache, "delete", side_effect=ConnectionError("down")):
            ratelimit.reset("gone", window=60)

    def test_a_refusal_always_asks_for_at_least_a_second(self):
        refused = ratelimit.RateLimited(0)
        self.assertEqual(refused.retry_after, 1)
        self.assertIn("1 s", str(refused))
        self.assertEqual(str(ratelimit.RateLimited(5, "slow down")), "slow down")


def _tile(visibility, link=None):
    return {"title": "Probe", "description": "d", "icon": "i", "link": link,
            "visibility": visibility}


class DashboardArmTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user("member", password="pw")
        self.staff = User.objects.create_user("staff", password="pw", is_staff=True)
        self.bare_root = User.objects.create_user("bareroot", password="pw", is_superuser=True)

    def shown(self, visibility, user, link=None):
        return _resolve_dashboard_item(_tile(visibility, link), user) is not None

    def test_a_staff_tile_is_for_staff_and_superusers_only(self):
        self.assertTrue(self.shown("staff", self.staff))
        self.assertTrue(self.shown("staff", self.bare_root))
        self.assertFalse(self.shown("staff", self.member))
        self.assertFalse(self.shown("staff", AnonymousUser()))

    def test_a_group_tile_is_for_the_group_and_superusers(self):
        Group.objects.create(name="boards").user_set.add(self.member)
        self.assertTrue(self.shown("group: boards ", self.member))
        self.assertFalse(self.shown("group:boards", self.staff))
        self.assertFalse(self.shown("group:boards", AnonymousUser()))
        self.assertTrue(self.shown("group:boards", self.bare_root))

    def test_a_superuser_tile_is_hidden_from_staff(self):
        self.assertFalse(self.shown("superuser", self.staff))
        self.assertFalse(self.shown("superuser", AnonymousUser()))

    def test_a_public_tile_is_for_everyone(self):
        self.assertTrue(self.shown("public", AnonymousUser()))

    def test_a_route_name_becomes_its_path_and_an_unknown_one_is_kept(self):
        resolved = _resolve_dashboard_item(_tile("public", "core:dashboard"), self.member)
        self.assertEqual(resolved["link"], reverse("core:dashboard"))
        with mock.patch("toto.core.views._plan_allows", return_value=True):
            kept = _resolve_dashboard_item(_tile("public", "nowhere:page"), self.member)
        self.assertEqual(kept["link"], "nowhere:page")

    def test_a_plain_address_is_never_a_plan_question(self):
        self.assertTrue(_plan_allows(self.member, None))
        self.assertTrue(_plan_allows(self.member, "https://grafana.example.org/"))

    def test_a_plan_check_that_breaks_keeps_the_tile(self):
        from django.apps import apps

        if not apps.is_installed("toto.subscriptions"):
            self.skipTest("no plans on this host")
        with mock.patch("toto.subscriptions.gate.is_entitled", side_effect=RuntimeError("x")):
            self.assertTrue(_plan_allows(self.member, "vault:index"))

    def test_a_tile_the_plan_withholds_is_hidden(self):
        with mock.patch("toto.core.views._plan_allows", return_value=False):
            self.assertFalse(self.shown("public", self.member, "vault:index"))

    def test_the_tile_carries_what_the_page_draws(self):
        resolved = _resolve_dashboard_item(_tile("public"), self.member)
        self.assertEqual(resolved, {"title": "Probe", "description": "d", "icon": "i",
                                    "link": None, "visibility": "public"})


class SmallPagesTests(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="T", author="t", publication_year=2026, active=True)
        self.client.force_login(User.objects.create_user("ada", password="pw"))

    def test_the_placeholder_and_maintenance_pages_render(self):
        self.assertEqual(self.client.get(reverse("core:not_implemented")).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:maintenance")).status_code, 200)


class EmailDeliveryTests(SimpleTestCase):
    def test_backends_that_discard_mail_cannot_deliver(self):
        for backend in email_config._NON_DELIVERING_EMAIL_BACKENDS:
            with self.subTest(backend=backend), override_settings(EMAIL_BACKEND=backend):
                self.assertFalse(email_config.email_delivery_configured())

    def test_smtp_and_the_test_outbox_can_deliver(self):
        for backend in ("django.core.mail.backends.smtp.EmailBackend",
                        "django.core.mail.backends.locmem.EmailBackend"):
            with self.subTest(backend=backend), override_settings(EMAIL_BACKEND=backend):
                self.assertTrue(email_config.email_delivery_configured())

    @override_settings(EMAIL_BACKEND="toto.jess.backend.JessEmailBackend")
    def test_pointing_at_a_mail_app_this_host_does_not_install_delivers_nothing(self):
        with mock.patch("django.apps.apps.is_installed", return_value=False):
            self.assertFalse(email_config.email_delivery_configured())


class BatchActionTests(SimpleTestCase):
    def test_each_row_succeeds_or_fails_on_its_own(self):
        def square(n):
            if n == 3:
                raise ValueError("three is bad")
            return n * n

        result = BatchAction([1, 2, 3, 4]).run(square)
        self.assertEqual(result.success, [1, 4, 16])
        self.assertEqual((result.success_count, result.failed_count), (3, 1))
        self.assertEqual(result.failed_titles, ["3"])
        self.assertEqual(result.errors, {"3": "three is bad"})

    def test_the_admin_is_told_each_failure_then_the_totals(self):
        said = []
        result = BatchActionResult(success=["a.txt"], failed=[("b.txt", OSError("gone"))])
        BatchAction.display_messages(result, lambda req, msg, level: said.append((level, msg)),
                                     None, verb="hash")
        self.assertEqual([level for level, _ in said], ["error", "info", "warning"])
        self.assertIn("Failed to hash 'b.txt': gone", said[0][1])
        self.assertIn("Hash 1 item(s): a.txt", said[1][1])
        self.assertIn("1 item(s) failed to hash", said[2][1])

    def test_a_clean_run_says_only_what_was_done(self):
        said = []
        BatchAction.display_messages(BatchActionResult(success=["x"], failed=[]),
                                     lambda req, msg, level: said.append(level), None)
        self.assertEqual(said, ["info"])


class ReadOnlyAdminTests(TestCase):
    def setUp(self):
        self.request = RequestFactory().get("/admin/")
        self.request.user = User.objects.create_superuser("root", password="pw")

    def test_a_read_only_admin_refuses_every_write_and_locks_every_field(self):
        admin = TotoModelAdmin(Platform, AdminSite())
        admin.readonly_flag = True
        self.assertFalse(admin.has_add_permission(self.request))
        self.assertFalse(admin.has_change_permission(self.request))
        self.assertFalse(admin.has_delete_permission(self.request))
        self.assertIn("site_name", admin.get_readonly_fields(self.request))

    def test_an_ordinary_admin_lets_a_superuser_write(self):
        admin = TotoModelAdmin(Platform, AdminSite())
        admin.readonly_flag = False
        self.assertTrue(admin.has_add_permission(self.request))
        self.assertTrue(admin.has_change_permission(self.request))
        self.assertEqual(list(admin.get_readonly_fields(self.request)), [])


@skipUnless(find_spec("cv2"),
            "OpenCV is not installed here: toto.core.qr draws and reads QR codes on the "
            "hosts that install it (zenobia does not, since 2026-10-01)")
class QRTests(SimpleTestCase):
    def png(self, data_uri):
        import base64

        prefix = "data:image/png;base64,"
        self.assertTrue(data_uri.startswith(prefix))
        return base64.b64decode(data_uri[len(prefix):])

    def test_a_pairing_string_survives_the_round_trip(self):
        ticket = "zenobia-pair:v1:" + "AbC123xyz-" * 10
        self.assertEqual(qr.read(self.png(qr.render_data_uri(ticket))), ticket)

    def test_a_small_scale_still_reads(self):
        self.assertEqual(qr.read(self.png(qr.render_data_uri("hello", scale=3))), "hello")

    def test_an_empty_string_is_never_drawn(self):
        with self.assertRaises(qr.QRError):
            qr.render_data_uri("")

    def test_no_upload_is_refused_by_name(self):
        with self.assertRaisesMessage(qr.QRError, "No image was uploaded."):
            qr.read(b"")

    def test_bytes_that_are_no_image_are_refused_by_name(self):
        with self.assertRaisesMessage(qr.QRError, "not an image"):
            qr.read(b"certainly not a png")

    def test_a_picture_without_a_code_says_so(self):
        import cv2
        import numpy as np

        ok, buf = cv2.imencode(".png", np.full((64, 64), 255, dtype=np.uint8))
        with self.assertRaisesMessage(qr.QRError, "No QR code was found"):
            qr.read(buf.tobytes())


class PluginRegistryTests(SimpleTestCase):
    """The registry every plugin family (profile, vault editor, header) inherits."""

    def setUp(self):
        from toto.core.plugin import BasePlugin

        class Family(BasePlugin):
            registry = {}

        self.Family = Family

    def test_only_members_of_the_family_may_register(self):
        from toto.core.plugin import BasePlugin

        class Stranger(BasePlugin):
            pass

        with self.assertRaises(TypeError):
            self.Family.register(Stranger)

    def test_a_key_is_registered_once(self):
        @self.Family.register
        class ExportPlugin(self.Family):
            pass

        class AnotherExport(self.Family):
            key = "ExportPlugin"

        with self.assertRaisesMessage(ValueError, "Plugin already registered: ExportPlugin"):
            self.Family.register(AnotherExport)

    def test_an_abstract_member_can_decline_to_register(self):
        class Abstract(self.Family):
            @classmethod
            def should_register(cls):
                return False

        self.assertIs(self.Family.register(Abstract), Abstract)
        self.assertEqual(self.Family.registry, {})

    def test_the_decorator_s_options_become_the_plugin_s_own(self):
        @self.Family.plugin(key="notes", title="Notes", order=5)
        class Anything(self.Family):
            pass

        plugin = self.Family.get("notes")
        self.assertEqual((plugin.get_key(), plugin.get_title(), plugin.get_order()),
                         ("notes", "Notes", 5))

    def test_a_title_defaults_to_the_class_name_without_plugin(self):
        class HistoryPlugin(self.Family):
            pass

        self.assertEqual(HistoryPlugin.get_title(), "History")

    def test_plugins_come_in_order_and_hidden_ones_drop_out(self):
        @self.Family.plugin(key="late", order=50)
        class Late(self.Family):
            pass

        @self.Family.plugin(key="early", order=1)
        class Early(self.Family):
            pass

        @self.Family.plugin(key="off", order=2, enabled=False)
        class Off(self.Family):
            pass

        @self.Family.plugin(key="shy", order=3)
        class Shy(self.Family):
            @staticmethod
            def visible_for_request(request):
                return False

        self.assertEqual([p.get_key() for p in self.Family.all()], ["early", "off", "shy", "late"])
        self.assertEqual([p.get_key() for p in self.Family.visible(request=object())],
                         ["early", "late"])
        self.assertEqual([p.get_key() for p in self.Family.visible()], ["early", "shy", "late"])
        self.assertIsNone(self.Family.get("off").render(request=object()))

    def test_a_plugin_s_own_context_wins_over_what_it_was_handed(self):
        @self.Family.plugin(key="ctx")
        class Ctx(self.Family):
            def get_context(self, **kwargs):
                return {"colour": "mine"}

        context = self.Family.get("ctx").build_context(
            base_context={"colour": "base", "page": "p"}, colour="given", extra=1)
        self.assertEqual((context["colour"], context["page"], context["extra"]), ("mine", "p", 1))

    def test_unregistering_forgets_the_key(self):
        @self.Family.plugin(key="gone")
        class Gone(self.Family):
            pass

        self.Family.unregister("gone")
        self.Family.unregister("never-there")
        self.assertIsNone(self.Family.get("gone"))
