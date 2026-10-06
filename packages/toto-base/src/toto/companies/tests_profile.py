"""Shareholdings on a profile (stage 65): a plugin section, "Company
shareholdings", with the company and its link, the quantity and the
percentage; only what the viewer may see, and nothing of it in the profile's
own code.

    manage.py test toto.companies.tests_profile
"""

from decimal import Decimal

from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory
from django.urls import reverse

from toto.companies.register import holdings_of
from toto.companies.testing import SIGNED_OUT, CompaniesTestCase, client_of, community
from toto.socialhub.plugins.profile_plugins import ProfilePlugin

SECTION = 'data-testid="profile-shareholdings"'


def profile_url(person, tab="") -> str:
    url = reverse("socialhub:profile_details", args=[person.slug])
    return f"{url}?tab={tab}" if tab else url


class ProfileTests(CompaniesTestCase):
    def setUp(self):
        self.hold_shares(self.acme, self.hold, 25)
        self.hold_shares(self.acme, self.mia, 75)
        self.hold_shares(self.globex, self.hold, 4)

    def page(self, viewer, person=None, tab=""):
        return client_of(viewer).get(profile_url(person or self.hold, tab))

    # -- what is shown -----------------------------------------------------

    def test_another_member_sees_company_quantity_and_percentage(self):
        response = self.page(self.stan_user)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, SECTION)
        self.assertContains(response, "Company shareholdings")
        text = response.content.decode()
        for expected in ("Acme", "Globex", "25.00%", "100.00%",
                         "/socialhub/communities/acme/?tab=shareholdings",
                         "/socialhub/communities/globex/?tab=shareholdings"):
            self.assertIn(expected, text)

    def test_the_owner_sees_their_own(self):
        response = self.page(self.hold_user)
        self.assertContains(response, SECTION)
        self.assertContains(response, "25.00%")

    def test_each_percentage_is_of_that_companys_recorded_shares(self):
        rows = holdings_of(self.hold, self.stan_user)
        self.assertEqual([(row.holding.community.slug, row.quantity, row.percentage)
                          for row in rows],
                         [("acme", 25, Decimal("25.00")), ("globex", 4, Decimal("100.00"))])

    def test_the_basis_is_said_on_the_profile_too(self):
        self.assertContains(self.page(self.stan_user), "not of its share capital")

    def test_a_person_with_no_holding_has_no_section(self):
        for viewer in (self.stan_user, self.mia_user, self.root_user):
            with self.subTest(viewer=viewer.username):
                response = self.page(viewer, self.stan)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, SECTION)
                self.assertNotContains(response, "Company shareholdings")

    def test_the_section_is_on_the_overview_alone(self):
        self.assertNotContains(self.page(self.stan_user, tab="communities"), SECTION)
        self.assertNotContains(self.page(self.stan_user, tab="activity"), SECTION)

    # -- what is not -------------------------------------------------------

    def test_a_holding_in_a_community_that_is_no_company_is_not_shown(self):
        self.acme.org_type = "guild"
        self.acme.save()
        response = self.page(self.stan_user)
        self.assertContains(response, SECTION)
        self.assertContains(response, "Globex")
        text = response.content.decode()
        section = text[text.index(SECTION):]
        section = section[:section.index("</section>")]
        self.assertNotIn("Acme", section)
        self.assertEqual([row.holding.community.slug
                          for row in holdings_of(self.hold, self.stan_user)], ["globex"])

    def test_with_only_such_holdings_there_is_no_section(self):
        for company in (self.acme, self.globex):
            company.org_type = "other"
            company.save()
        for viewer in (self.stan_user, self.hold_user, self.root_user):
            with self.subTest(viewer=viewer.username):
                self.assertNotContains(self.page(viewer), SECTION)
        self.assertEqual(holdings_of(self.hold, self.root_user), [])

    def test_a_visitor_is_given_no_holding(self):
        self.assertEqual(holdings_of(self.hold, AnonymousUser()), [])
        self.assertEqual(holdings_of(self.hold, None), [])
        response = Client().get(profile_url(self.hold))
        self.assertIn(response.status_code, SIGNED_OUT)

    def test_the_plugin_declines_a_visitor_itself(self):
        request = RequestFactory().get(profile_url(self.hold))
        request.user = AnonymousUser()
        plugin = ProfilePlugin.get("company_shareholdings")
        self.assertFalse(plugin.is_visible(request=request, profile=self.hold))
        self.assertIsNone(plugin.render(request=request, profile=self.hold))

    def test_nobody_and_a_person_not_saved_have_none(self):
        from toto.people.models import Person

        self.assertEqual(holdings_of(None, self.stan_user), [])
        self.assertEqual(holdings_of(Person(display_name="New"), self.stan_user), [])

    def test_a_profile_shows_its_own_persons_holdings_only(self):
        response = self.page(self.stan_user, self.mia)
        text = response.content.decode()
        section = text[text.index(SECTION):]
        section = section[:section.index("</section>")]
        self.assertIn("75.00%", section)
        self.assertNotIn("Globex", section)
        self.assertNotIn("25.00%", section)

    def test_a_companys_name_reaches_the_profile_as_text(self):
        nasty = community("<i>Nasty</i> Ltd", head=self.head)
        self.hold_shares(nasty, self.hold, 1)
        text = self.page(self.stan_user).content.decode()
        self.assertNotIn("<i>Nasty</i>", text)
        self.assertIn("&lt;i&gt;Nasty&lt;/i&gt; Ltd", text)

    def test_a_holding_of_zero_is_listed_with_its_zero(self):
        self.hold_shares(self.acme, self.stan, 0)
        response = self.page(self.mia_user, self.stan)
        self.assertContains(response, SECTION)
        self.assertContains(response, "0.00%")

    # -- where the code is -------------------------------------------------

    def test_it_is_a_profile_plugin_on_the_overview(self):
        plugin = ProfilePlugin.get("company_shareholdings")
        self.assertIsNotNone(plugin)
        self.assertEqual(plugin.get_tab(), "overview")
        self.assertEqual(str(plugin.get_title()), "Company shareholdings")
        self.assertEqual(type(plugin).__module__, "toto.companies.plugins.profile_plugins")

    def test_the_profiles_own_code_knows_nothing_of_companies(self):
        from pathlib import Path

        import toto.socialhub

        root = Path(toto.socialhub.__file__).parent
        files = [root / "views" / "profile.py", root / "views" / "account.py",
                 root / "plugins" / "profile_plugins.py"]
        files += sorted((root / "templates" / "socialhub").glob("*profile*.html"))
        self.assertGreater(len(files), 6)
        for path in files:
            text = path.read_text(encoding="utf-8").lower()
            for word in ("shareholding", "toto.companies", "share_holdings", "sharehold"):
                self.assertNotIn(word, text, path.name)

    def test_without_the_plugin_the_profile_draws_and_shows_none(self):
        plugin = ProfilePlugin.registry.pop("company_shareholdings")
        self.addCleanup(ProfilePlugin.registry.__setitem__, "company_shareholdings", plugin)
        response = self.page(self.stan_user)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, SECTION)
