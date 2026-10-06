"""A company on its community page (stage 65): the "Company" section with
the ID number and the Shareholdings tab, for a company alone; the forms for
who may manage alone; every name as text.

    manage.py test toto.companies.tests_company_page
"""

from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory

from toto.companies.models import CompanyRecord
from toto.companies.testing import (SIGNED_OUT, CompaniesTestCase, client_of, community,
                                    member, page_url)
from toto.socialhub.plugins.community_plugins import CommunityPlugin

IDENTITY = 'data-testid="company-identity"'
REGISTER = 'data-testid="company-shareholdings"'
NUMBER_FORM = 'data-testid="company-number-form"'
HOLDING_FORM = 'data-testid="company-holding-form"'


class OrdinaryCommunityTests(CompaniesTestCase):
    def test_an_ordinary_community_has_no_company_section(self):
        for user in (self.mia_user, self.head_user, self.root_user):
            with self.subTest(user=user.username):
                response = client_of(user).get(page_url(self.guild))
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, IDENTITY)
                self.assertNotContains(response, "Company ID number")
                self.assertNotContains(response, NUMBER_FORM)

    def test_an_ordinary_community_has_no_shareholdings_tab(self):
        for user in (self.mia_user, self.head_user, self.root_user):
            with self.subTest(user=user.username):
                response = client_of(user).get(page_url(self.guild))
                keys = [tab["key"] for tab in response.context["community_tabs"]]
                self.assertNotIn("shareholdings", keys)
                self.assertNotContains(response, "tab=shareholdings")

    def test_asking_it_for_the_tab_is_its_overview(self):
        self.hold_shares(self.guild, self.hold, 5)      # rows left from a company's days
        response = client_of(self.head_user).get(page_url(self.guild, "shareholdings"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_community_tab"], "")
        self.assertNotContains(response, REGISTER)
        self.assertContains(response, "People connected to this community.")

    def test_every_kind_but_company_is_ordinary(self):
        from toto.socialhub.models import Community

        for kind, _label in Community.ORG_TYPES:
            made = community(f"Kind {kind}", head=self.head, org_type=kind)
            response = client_of(self.head_user).get(page_url(made))
            with self.subTest(kind=kind):
                self.assertEqual(IDENTITY in response.content.decode(), kind == "company")

    def test_its_page_still_has_what_it_had(self):
        self.mia.communities.add(self.guild)
        response = client_of(self.mia_user).get(page_url(self.guild))
        for expected in ("Guild", "Members", "Mia", "Org Chart", "Established"):
            self.assertContains(response, expected)


class CompanyPageTests(CompaniesTestCase):
    def test_the_company_section_shows_the_id_number(self):
        CompanyRecord.objects.create(community=self.acme, id_number="0000123456")
        response = client_of(self.mia_user).get(page_url(self.acme))
        self.assertContains(response, IDENTITY)
        self.assertContains(response, "Company ID number")
        self.assertContains(response, "0000123456")

    def test_without_a_number_it_says_so(self):
        response = client_of(self.mia_user).get(page_url(self.acme))
        self.assertContains(response, IDENTITY)
        self.assertContains(response, "Not recorded.")

    def test_the_company_keeps_the_community_page(self):
        response = client_of(self.mia_user).get(page_url(self.acme))
        for expected in ("Acme", "Company", "Members", "Mia", "Hugo", "Org Chart"):
            self.assertContains(response, expected)

    def test_the_strip_has_the_shareholdings_tab(self):
        response = client_of(self.stan_user).get(page_url(self.acme))
        tabs = {tab["key"]: tab for tab in response.context["community_tabs"]}
        self.assertIn("shareholdings", tabs)
        self.assertEqual(str(tabs["shareholdings"]["label"]), "Shareholdings")
        self.assertContains(response, 'id="community-tab-shareholdings"')
        self.assertNotContains(response, REGISTER)       # on its tab, not on the Overview

    def test_the_tab_lists_a_holder_who_is_no_member(self):
        self.hold_shares(self.acme, self.hold, 12)
        response = client_of(self.mia_user).get(page_url(self.acme, "shareholdings"))
        self.assertContains(response, REGISTER)
        self.assertContains(response, "/socialhub/profiles/hold/")
        self.assertContains(response, "100.00%")

    def test_the_table_scrolls_inside_its_own_box(self):
        self.hold_shares(self.acme, self.hold, 12)
        text = client_of(self.mia_user).get(page_url(self.acme, "shareholdings")) \
            .content.decode()
        # The table's own box scrolls (and may shrink beside the ring).
        box = text.index('<div class="min-w-0 overflow-x-auto lg:order-1">')
        table = text.index('data-testid="company-register"')
        self.assertLess(box, table)
        self.assertNotIn("<div", text[box + 5:table], "nothing between the box and its table")

    # -- the forms ---------------------------------------------------------

    def test_who_may_not_manage_gets_no_form(self):
        holding = self.hold_shares(self.acme, self.hold, 12)
        for user in (self.mia_user, self.sen_user, self.stan_user, self.hold_user,
                     self.stef_user, self.other_head_user):
            with self.subTest(user=user.username):
                client = client_of(user)
                overview = client.get(page_url(self.acme))
                tab = client.get(page_url(self.acme, "shareholdings"))
                self.assertNotContains(overview, NUMBER_FORM)
                self.assertNotContains(overview, self.number_url(self.acme))
                self.assertNotContains(tab, HOLDING_FORM)
                self.assertNotContains(tab, self.holding_url(self.acme))
                self.assertNotContains(tab, self.delete_url(self.acme, holding.pk))
                self.assertNotContains(tab, 'name="person"')
                self.assertNotContains(tab, 'name="quantity"')

    def test_the_head_and_an_administrator_get_the_forms(self):
        holding = self.hold_shares(self.acme, self.hold, 12)
        for user in (self.head_user, self.root_user):
            with self.subTest(user=user.username):
                client = client_of(user)
                overview = client.get(page_url(self.acme))
                tab = client.get(page_url(self.acme, "shareholdings"))
                self.assertContains(overview, NUMBER_FORM)
                self.assertContains(overview, self.number_url(self.acme))
                self.assertContains(tab, HOLDING_FORM)
                self.assertContains(tab, self.delete_url(self.acme, holding.pk))
                self.assertContains(tab, 'value="stan"')        # any person may be chosen
                self.assertContains(tab, "csrfmiddlewaretoken")

    # -- names as text -----------------------------------------------------

    def test_names_and_the_number_reach_the_page_as_text(self):
        _user, evil = member("evil", name="<script>alert(1)</script>")
        nasty = community("<b>Nasty</b> & Co", head=self.head)
        CompanyRecord.objects.create(community=nasty, id_number='"><img src=x onerror=1>')
        self.hold_shares(nasty, evil, 3)
        client = client_of(self.head_user)
        for response in (client.get(page_url(nasty)),
                         client.get(page_url(nasty, "shareholdings"))):
            text = response.content.decode()
            self.assertNotIn("<script>alert(1)</script>", text)
            self.assertNotIn("<b>Nasty</b>", text)
            self.assertNotIn("<img src=x", text)
        self.assertContains(client.get(page_url(nasty, "shareholdings")),
                            "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertContains(client.get(page_url(nasty)),
                            "&quot;&gt;&lt;img src=x onerror=1&gt;")

    # -- a visitor ---------------------------------------------------------

    def test_a_visitor_gets_no_page_here_or_no_company_on_it(self):
        CompanyRecord.objects.create(community=self.acme, id_number="0000123456")
        self.hold_shares(self.acme, self.hold, 12)
        for tab in ("", "shareholdings"):
            response = Client().get(page_url(self.acme, tab))
            if response.status_code in SIGNED_OUT:
                continue
            self.assertNotContains(response, IDENTITY)
            self.assertNotContains(response, REGISTER)
            self.assertNotContains(response, "0000123456")

    def test_the_sections_decline_a_visitor_themselves(self):
        """Without any sign-in gate in front: the plugins' own answer."""
        request = RequestFactory().get(page_url(self.acme))
        request.user = AnonymousUser()
        for key in ("company_identity", "company_shareholdings"):
            plugin = CommunityPlugin.get(key)
            self.assertFalse(plugin.is_visible(request=request, community=self.acme), key)
            self.assertIsNone(plugin.render(request=request, community=self.acme), key)
        self.assertEqual(CommunityPlugin.tabs_shown(request=request, community=self.acme)
                         and [tab["key"] for tab in CommunityPlugin.tabs_shown(
                             request=request, community=self.acme)
                             if tab["key"] == "shareholdings"], [])

    def test_the_two_sections_are_plugins_and_socialhub_names_no_company_app(self):
        from pathlib import Path

        import toto.socialhub

        self.assertEqual(CommunityPlugin.get("company_shareholdings").get_tab(),
                         "shareholdings")
        self.assertEqual(CommunityPlugin.get("company_identity").get_tab(), "")
        root = Path(toto.socialhub.__file__).parent
        for path in list(root.rglob("*.py")) + list(root.rglob("*.html")):
            if path.name.startswith("tests"):
                continue
            self.assertNotIn("toto.companies", path.read_text(encoding="utf-8"), path.name)
