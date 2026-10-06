"""Tabs for plugin sections on the community page (2026-10-06, stage 65).

A ``CommunityPlugin`` that names a ``tab`` is drawn on a tab of its own
(``?tab=<name>``); the strip is there only when such a plugin shows for this
community and this viewer, so a community with none looks as it always did.
A tab the viewer is not shown is the Overview.

    manage.py test toto.socialhub.tests_community_tabs
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils.safestring import mark_safe

from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Community
from toto.socialhub.plugins.community_plugins import CommunityPlugin
from toto.socialhub.views.community import community_page_url, community_tabs

User = get_user_model()

STRIP = 'data-testid="community-tabs"'


class _Tabbed(CommunityPlugin):
    """A section on a tab of its own, shown for communities named in
    ``only`` (every community when empty) and hidden from ``hidden_from``."""

    key = "test_tabbed"
    title = "Ledger <b>"
    tab = "testtab"
    order = 40
    only: set = set()
    hidden_from: set = set()

    def is_visible(self, **kwargs) -> bool:
        if not super().is_visible(**kwargs):
            return False
        community = kwargs["community"]
        if self.only and community.slug not in self.only:
            return False
        user = getattr(kwargs.get("request"), "user", None)
        return getattr(user, "username", "") not in self.hidden_from

    def render_html(self, **kwargs) -> str:
        return mark_safe('<section data-testid="tabbed-section">on a tab</section>')


class _Second(_Tabbed):
    """A second section on the same tab: one tab, two sections."""

    key = "test_tabbed_second"
    title = "Ledger notes"
    order = 41

    def render_html(self, **kwargs) -> str:
        return mark_safe('<section data-testid="tabbed-second">also on the tab</section>')


class _Plain(CommunityPlugin):
    """A section with no tab: on the Overview, as every plugin was."""

    key = "test_plain"
    title = "Plain"
    order = 42

    def render_html(self, **kwargs) -> str:
        return mark_safe('<section data-testid="plain-section">on the page</section>')


class CommunityTabsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="T", author="A", publication_year=2026,
                                active=True)
        cls.user = User.objects.create_user("tab-mia", password="pw")
        cls.person = Person.objects.create(user=cls.user, display_name="Mia")
        cls.other_user = User.objects.create_user("tab-otto", password="pw")
        Person.objects.create(user=cls.other_user, display_name="Otto")
        cls.guild = Community.objects.create(name="Tab Guild")
        cls.plain = Community.objects.create(name="Plain Guild")
        cls.person.communities.add(cls.guild)

    def setUp(self):
        for plugin in (_Tabbed, _Second, _Plain):
            CommunityPlugin.register(plugin)
            self.addCleanup(CommunityPlugin.unregister, plugin.key)
        _Tabbed.only = {self.guild.slug}
        _Tabbed.hidden_from = set()
        self.addCleanup(setattr, _Tabbed, "only", set())
        self.addCleanup(setattr, _Tabbed, "hidden_from", set())
        self.client.force_login(self.user)

    def page(self, community, tab=None, user=None):
        if user is not None:
            self.client.force_login(user)
        data = {} if tab is None else {"tab": tab}
        return self.client.get(
            reverse("socialhub:community_detail", args=[community.slug]), data)

    # -- no tabbed plugin: the page as it was ------------------------------

    def test_a_community_no_tabbed_plugin_shows_for_has_no_strip(self):
        response = self.page(self.plain)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, STRIP)
        self.assertEqual(response.context["community_tabs"], [])
        self.assertEqual(response.context["active_community_tab"], "")
        self.assertContains(response, 'data-testid="plain-section"')
        self.assertContains(response, "People connected to this community.")

    def test_asking_such_a_community_for_a_tab_is_the_overview(self):
        response = self.page(self.plain, "testtab")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, STRIP)
        self.assertNotContains(response, 'data-testid="tabbed-section"')
        self.assertContains(response, "People connected to this community.")

    # -- with one ----------------------------------------------------------

    def test_the_strip_has_the_overview_and_the_plugins_tab(self):
        response = self.page(self.guild)
        self.assertContains(response, STRIP)
        tabs = response.context["community_tabs"]
        self.assertEqual([tab["key"] for tab in tabs], ["", "testtab"])
        self.assertEqual([tab["active"] for tab in tabs], [True, False])
        self.assertEqual(tabs[0]["url"], community_page_url(self.guild))
        self.assertEqual(tabs[1]["url"], community_page_url(self.guild) + "?tab=testtab")

    def test_the_overview_keeps_the_old_sections_and_not_the_tabs(self):
        response = self.page(self.guild)
        self.assertContains(response, 'data-testid="plain-section"')
        self.assertContains(response, "People connected to this community.")
        self.assertNotContains(response, 'data-testid="tabbed-section"')
        self.assertNotContains(response, 'data-testid="tabbed-second"')

    def test_the_tab_draws_its_sections_alone(self):
        response = self.page(self.guild, "testtab")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_community_tab"], "testtab")
        self.assertContains(response, 'data-testid="tabbed-section"')
        self.assertContains(response, 'data-testid="tabbed-second"')
        self.assertNotContains(response, 'data-testid="plain-section"')
        self.assertNotContains(response, "People connected to this community.")
        # The header and the hero stay on every tab.
        self.assertContains(response, self.guild.name)

    def test_two_plugins_on_one_tab_are_one_tab_named_by_the_first(self):
        tabs = self.page(self.guild).context["community_tabs"]
        self.assertEqual(len(tabs), 2)
        self.assertEqual(tabs[1]["label"], "Ledger <b>")

    def test_a_tab_title_names_the_tab_where_given(self):
        _Tabbed.tab_title = "Books"
        self.addCleanup(setattr, _Tabbed, "tab_title", "")
        tabs = self.page(self.guild).context["community_tabs"]
        self.assertEqual(tabs[1]["label"], "Books")

    def test_the_tabs_name_reaches_the_page_as_text(self):
        response = self.page(self.guild)
        self.assertContains(response, "Ledger &lt;b&gt;")
        self.assertNotContains(response, "Ledger <b>")

    def test_an_unknown_tab_is_the_overview(self):
        response = self.page(self.guild, "nothing-here")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_community_tab"], "")
        self.assertContains(response, "People connected to this community.")

    def test_a_tab_hidden_from_a_viewer_is_the_overview_and_is_not_built(self):
        _Tabbed.hidden_from = {self.other_user.username}
        response = self.page(self.guild, "testtab", user=self.other_user)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, STRIP)
        self.assertNotContains(response, 'data-testid="tabbed-section"')
        self.assertContains(response, "People connected to this community.")
        # The other viewer still has it.
        self.assertContains(self.page(self.guild, "testtab", user=self.user),
                            'data-testid="tabbed-section"')

    def test_the_active_tab_is_marked_for_a_reader(self):
        response = self.page(self.guild, "testtab")
        self.assertContains(response, 'id="community-tab-testtab"')
        self.assertContains(response, 'id="community-tab-overview"')
        self.assertEqual(response.content.decode().count('aria-current="page"'), 1)

    # -- the helpers -------------------------------------------------------

    def test_tabs_shown_renders_nothing(self):
        calls = []

        class _Counting(_Tabbed):
            key = "test_counting"
            tab = "counting"

            def render_html(self, **kwargs):
                calls.append(1)
                return ""

        CommunityPlugin.register(_Counting)
        self.addCleanup(CommunityPlugin.unregister, _Counting.key)
        request = self.client.get("/").wsgi_request
        request.user = self.user
        shown = CommunityPlugin.tabs_shown(request=request, community=self.guild)
        self.assertIn("counting", [tab["key"] for tab in shown])
        self.assertEqual(calls, [])

    def test_community_tabs_ignores_a_tab_param_that_is_not_shown(self):
        request = self.client.get("/", {"tab": "testtab"}).wsgi_request
        request.user = self.user
        self.assertEqual(community_tabs(request, self.plain),
                         {"community_tabs": [], "active_community_tab": ""})

    def test_every_shipped_plugin_without_a_tab_is_on_the_overview(self):
        for plugin in CommunityPlugin.on_tab(""):
            self.assertEqual(plugin.get_tab(), "")
        self.assertIn("test_plain", [plugin.get_key() for plugin in CommunityPlugin.on_tab("")])
        self.assertEqual([plugin.get_key() for plugin in CommunityPlugin.on_tab("testtab")],
                         ["test_tabbed", "test_tabbed_second"])
