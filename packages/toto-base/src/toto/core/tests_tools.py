"""The Tools hub: what this host can DO to a file, as opposed to what it keeps.

Tools were tabs in Office's strip from 2026-08-29 to 2026-09-01, moved out the
day the set outgrew the claim "the things Office does", and outlived it: Office
itself was retired to limbo on 2026-09-02 and `core/office.py`'s Tools half
became `core/tools.py`.

What this file asserts is what the hub has to be worth: it offers exactly the
tools that are installed AND mounted, it never advertises a door that leads
nowhere, and it refuses to be an empty room.

A host need not mount the hub at all — zenobia retired it on 2026-09-07 and
mounts nothing at /tools/ (its urls.py says why) — so these tests mount it
themselves, in front of the host's own routes, rather than assume the host
does (2026-10-02, 41.4: every one of them errored on zenobia's settings).
"""
from __future__ import annotations

from importlib import import_module

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, include, path, reverse

from toto.core import tools as tools_hub
from toto.core.models import Platform

User = get_user_model()

#: The hub at /tools/, then every route of the host's (its pages' header and
#: sign-in links reverse them).
urlpatterns = [
    path("tools/", include("toto.core.tools_urls")),
    *import_module(settings.ROOT_URLCONF).urlpatterns,
]


@override_settings(ROOT_URLCONF=__name__)
class ToolsHubTestCase(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Test Platform", author="Tests",
                                publication_year=2026, active=True)
        self.user = User.objects.create_user("worker", password="pw")
        self.client.force_login(self.user)

    def url(self):
        return reverse("tools:index")


class HubTests(ToolsHubTestCase):
    def test_login_is_required(self):
        self.client.logout()
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_every_available_tool_is_offered(self):
        body = self.client.get(self.url()).content.decode()
        tools = tools_hub.available_tools()
        if not tools:
            self.skipTest("no tool app installed on this build")
        for tool in tools:
            with self.subTest(tool=tool["slug"]):
                self.assertIn(tool["label"], body)
                self.assertIn(tool["url"], body)

    def test_a_tool_whose_app_is_absent_is_not_offered(self):
        """An offer that leads nowhere is worse than no offer."""
        ghost = tools_hub.Tool(slug="ghost", label="Ghost",
                               icon="fa-solid fa-ghost", url_name="nowhere:home",
                               app_labels=("toto.not_installed",))
        self.assertNotIn(ghost.slug,
                         [t["slug"] for t in tools_hub.available_tools()])

    def test_a_tool_that_is_installed_but_unmounted_is_not_offered(self):
        """`url_name` is resolved lazily and dropped on NoReverseMatch. A host
        can install an app and mount no URL for it at all, and the hub must
        render short rather than 500."""
        from unittest import mock

        # `django.urls.reverse`, not `toto.core.tools.reverse`: available_tools
        # imports it INSIDE the function, so the module attribute is never the
        # one called and patching it asserts nothing. The (retired) Office
        # strip test settled the same way.
        with mock.patch("django.urls.reverse", side_effect=NoReverseMatch):
            self.assertEqual(tools_hub.available_tools(), [])

    def test_an_empty_hub_redirects_rather_than_rendering_a_dead_room(self):
        from unittest import mock

        with mock.patch("toto.core.tools.available_tools", return_value=[]):
            response = self.client.get(self.url())
        self.assertEqual(response.status_code, 302)

    def test_the_hub_is_read_only(self):
        """Tools is not in the plan catalogue, so `SubscriptionGateMiddleware`
        leaves it free — which is correct exactly as long as every route here is
        a GET. Each tool keeps its own entitlement and its own POSTs in its own
        app, which is what `Tool.entitlement` records."""
        self.assertEqual(self.client.post(self.url()).status_code, 405)


class SeparationTests(ToolsHubTestCase):
    """What must hold now that the hub stands alone. (Two tests asserting
    Office and Tools never claimed the same slug retired with Office — there
    is no Office strip left to keep tools out of.)"""

    def test_every_tool_declares_its_own_entitlement(self):
        """The hub is free; the work is not. A tool with no entitlement would be
        a paywalled app reachable through an unpaywalled door."""
        for tool in tools_hub.TOOLS:
            with self.subTest(tool=tool.slug):
                self.assertTrue(tool.entitlement,
                                f"{tool.slug} names no entitlement")
