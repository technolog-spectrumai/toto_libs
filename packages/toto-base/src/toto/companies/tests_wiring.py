"""How the companies app hangs on the platform (stage 65): a list a host
installs, a free entitlement, a table in "Download my data", and nothing in
the Django admin.

    manage.py test toto.companies.tests_wiring
"""

from unittest import skipUnless

from django.apps import apps
from django.core import checks

from toto.companies.testing import CompaniesTestCase


class WiringTests(CompaniesTestCase):
    def test_a_host_installs_it_from_its_own_list(self):
        from toto import registry

        self.assertEqual(registry.COMPANIES_APPS, ["toto.companies"])
        self.assertNotIn("toto.companies", registry.CORE_APPS)
        self.assertNotIn("toto.companies", registry.BASE_APPS)

    def test_the_old_company_app_is_another_app(self):
        self.assertEqual(apps.get_app_config("companies").name, "toto.companies")
        self.assertEqual({model.__name__ for model in
                          apps.get_app_config("companies").get_models()},
                         {"CompanyRecord", "ShareHolding"})

    @skipUnless(apps.is_installed("toto.subscriptions"), "no plans on this host")
    def test_it_is_free_on_every_plan(self):
        from toto.subscriptions import plans
        from toto.subscriptions.catalogue import registry

        entitlement = registry.get("companies")
        self.assertIsNotNone(entitlement)
        self.assertTrue(entitlement.free)
        for plan in plans.all_plans():
            self.assertNotIn("companies", plan.features, plan.key)

    @skipUnless(apps.is_installed("toto.subscriptions"), "no plans on this host")
    def test_the_plan_checks_stay_quiet(self):
        from toto.subscriptions.checks import check_subscription_plans

        findings = [finding for finding in check_subscription_plans(None)
                    if isinstance(finding, (checks.Error, checks.Warning))
                    and "companies" in str(finding.msg)]
        self.assertEqual(findings, [])

    @skipUnless(apps.is_installed("toto.subscriptions"), "no plans on this host")
    def test_a_member_on_the_free_plan_is_entitled(self):
        from toto.subscriptions.gate import is_entitled

        for user in (self.mia_user, self.head_user, self.hold_user):
            self.assertTrue(is_entitled(user, "companies"))

    def test_the_doors_are_in_the_namespace_the_entitlement_names(self):
        from django.urls import resolve

        match = resolve(self.holding_url(self.acme))
        self.assertEqual(match.app_name, "companies")

    def test_download_my_data_holds_a_members_holdings(self):
        from toto.core.personal_data import PersonalDataPlugin

        self.hold_shares(self.acme, self.hold, 12)
        self.hold_shares(self.globex, self.hold, 3)
        self.hold_shares(self.acme, self.mia, 99)
        from toto.companies.plugins import personal_data_plugins  # noqa: F401 - registers

        plugin = PersonalDataPlugin.get("companies")
        (table,) = plugin.tables(self.hold_user)
        self.assertEqual(table.name, "companies_shareholdings")
        self.assertEqual([(row["company"], row["company_slug"], row["shares"])
                          for row in table.rows],
                         [("Acme", "acme", 12), ("Globex", "globex", 3)])
        self.assertEqual(set(table.rows[0]),
                         {"company", "company_slug", "shares", "recorded", "changed"})

    def test_it_holds_them_also_where_the_community_is_no_company_now(self):
        from toto.companies.plugins.personal_data_plugins import CompaniesData

        self.hold_shares(self.acme, self.hold, 12)
        self.acme.org_type = "guild"
        self.acme.save()
        (table,) = CompaniesData().tables(self.hold_user)
        self.assertEqual([row["shares"] for row in table.rows], [12])

    def test_a_member_with_none_and_a_user_with_no_person_get_an_empty_table(self):
        from django.contrib.auth import get_user_model

        from toto.companies.plugins.personal_data_plugins import CompaniesData

        bare = get_user_model().objects.create_user("bare-user", password="pw")
        for user in (self.stan_user, bare):
            (table,) = CompaniesData().tables(user)
            self.assertEqual(table.rows, [])
