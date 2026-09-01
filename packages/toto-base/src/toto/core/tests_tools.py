"""The Tools hub: what this host can DO to a file, as opposed to what it keeps.

Tools were tabs in Office's strip from 2026-08-29 to 2026-09-01. The move out is
not a reversal of that decision, it is the same one applied once the set outgrew
the claim: a strip reading "the things Office does" holds for reading a scan you
already keep here and stops holding for rendering HTML you paste in.

What this file asserts is what the split has to be worth: the hub offers exactly
the tools that are installed AND mounted, it never advertises a door that leads
nowhere, and it refuses to be an empty room.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from toto.core import office
from toto.core.models import Platform

User = get_user_model()


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
        tools = office.available_tools()
        if not tools:
            self.skipTest("no tool app installed on this build")
        for tool in tools:
            with self.subTest(tool=tool["slug"]):
                self.assertIn(tool["label"], body)
                self.assertIn(tool["url"], body)

    def test_a_tool_whose_app_is_absent_is_not_offered(self):
        """The same guarantee `available_sections` gives Office: an offer that
        leads nowhere is worse than no offer."""
        ghost = office.Tool(slug="ghost", label="Ghost",
                            icon="fa-solid fa-ghost", url_name="nowhere:home",
                            app_labels=("toto.not_installed",))
        self.assertNotIn(ghost.slug,
                         [t["slug"] for t in office.available_tools()])

    def test_a_tool_that_is_installed_but_unmounted_is_not_offered(self):
        """`url_name` is resolved lazily and dropped on NoReverseMatch. A host
        can install an app and mount no URL for it at all, and the hub must
        render short rather than 500."""
        from unittest import mock

        # `django.urls.reverse`, not `toto.core.office.reverse`: available_tools
        # imports it INSIDE the function, so the module attribute is never the
        # one called and patching it asserts nothing. The Office strip test
        # settled the same way.
        with mock.patch("django.urls.reverse", side_effect=NoReverseMatch):
            self.assertEqual(office.available_tools(), [])

    def test_an_empty_hub_redirects_rather_than_rendering_a_dead_room(self):
        from unittest import mock

        with mock.patch("toto.core.office.available_tools", return_value=[]):
            response = self.client.get(self.url())
        self.assertEqual(response.status_code, 302)

    def test_the_hub_is_read_only(self):
        """Tools is not in the plan catalogue, so `SubscriptionGateMiddleware`
        leaves it free — which is correct exactly as long as every route here is
        a GET. Each tool keeps its own entitlement and its own POSTs in its own
        app, which is what `Tool.entitlement` records."""
        self.assertEqual(self.client.post(self.url()).status_code, 405)


class SeparationTests(ToolsHubTestCase):
    """The half that would rot silently: Office and Tools must not both claim
    the same thing."""

    def test_no_tool_is_also_an_office_section(self):
        section_slugs = {s.slug for s in office.SECTIONS}
        for tool in office.TOOLS:
            with self.subTest(tool=tool.slug):
                self.assertNotIn(tool.slug, section_slugs)

    def test_the_office_strip_offers_no_tools(self):
        self.assertFalse(any(t.get("is_tool") for t in office.office_tabs()),
                         "a tool reached the Office strip — they live here now")

    def test_every_tool_declares_its_own_entitlement(self):
        """The hub is free; the work is not. A tool with no entitlement would be
        a paywalled app reachable through an unpaywalled door."""
        for tool in office.TOOLS:
            with self.subTest(tool=tool.slug):
                self.assertTrue(tool.entitlement,
                                f"{tool.slug} names no entitlement")
