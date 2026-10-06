"""Who may change a company's ID number and its register (stage 65): the
community's head and administrators, checked by every door on the server,
with the company and the holding taken from the address.

    manage.py test toto.companies.tests_permissions
"""

from django.test import Client

from toto.companies import access
from toto.companies.models import CompanyRecord, ShareHolding
from toto.companies.testing import SIGNED_OUT, CompaniesTestCase, client_of


class RuleTests(CompaniesTestCase):
    def test_a_company_is_a_community_of_that_kind(self):
        self.assertTrue(access.is_company(self.acme))
        self.assertFalse(access.is_company(self.guild))
        self.assertFalse(access.is_company(None))

    def test_the_head_manages_their_own_company_only(self):
        self.assertTrue(access.may_manage(self.head_user, self.acme))
        self.assertFalse(access.may_manage(self.head_user, self.globex))
        self.assertTrue(access.may_manage(self.other_head_user, self.globex))

    def test_an_administrator_manages_every_company(self):
        self.assertTrue(access.may_manage(self.root_user, self.acme))
        self.assertTrue(access.may_manage(self.root_user, self.globex))

    def test_nobody_else_manages(self):
        from django.contrib.auth.models import AnonymousUser

        for user in (self.mia_user, self.sen_user, self.stan_user, self.hold_user,
                     self.stef_user, AnonymousUser(), None):
            with self.subTest(user=getattr(user, "username", user)):
                self.assertFalse(access.may_manage(user, self.acme))

    def test_staff_alone_is_not_enough(self):
        self.assertTrue(self.stef_user.is_staff)
        self.assertFalse(access.may_manage(self.stef_user, self.acme))

    def test_a_superuser_without_the_plan_does_not_manage(self):
        from toto.socialhub.contact_access import is_administrator

        if is_administrator(self.late_user):
            self.skipTest("this host sells no Superuser plan: the privilege alone decides")
        self.assertFalse(access.may_manage(self.late_user, self.acme))

    def test_nobody_manages_an_ordinary_community_here(self):
        for user in (self.head_user, self.root_user):
            self.assertFalse(access.may_manage(user, self.guild))

    def test_every_signed_in_viewer_sees_a_companys_register(self):
        from django.contrib.auth.models import AnonymousUser

        for user in (self.head_user, self.mia_user, self.stan_user, self.hold_user):
            self.assertTrue(access.may_see_register(user, self.acme))
            self.assertFalse(access.may_see_register(user, self.guild))
        self.assertFalse(access.may_see_register(AnonymousUser(), self.acme))
        self.assertFalse(access.may_see_register(None, self.acme))


class DoorTests(CompaniesTestCase):
    def setUp(self):
        self.holding = self.hold_shares(self.acme, self.hold, 10)
        self.foreign = self.hold_shares(self.globex, self.stan, 77)

    def doors(self, company=None, pk=None):
        """Each door of ``company`` with a body that would change something."""
        company = company or self.acme
        return [
            ("number", self.number_url(company), {"id_number": "0000123456"}),
            ("save", self.holding_url(company), {"person": self.mia.slug, "quantity": "5"}),
            ("change", self.holding_url(company), {"person": self.hold.slug, "quantity": "99"}),
            ("delete", self.delete_url(company, pk or self.holding.pk), {}),
        ]

    def state(self):
        return (sorted(ShareHolding.objects.values_list("community__slug", "person__slug",
                                                        "quantity")),
                sorted(CompanyRecord.objects.values_list("community__slug", "id_number")))

    def assert_refused(self, user, status, company=None, pk=None):
        before = self.state()
        client = client_of(user) if user is not None else Client()
        for name, url, body in self.doors(company, pk):
            with self.subTest(door=name, user=getattr(user, "username", None)):
                response = client.post(url, body)
                if isinstance(status, tuple):
                    self.assertIn(response.status_code, status)
                else:
                    self.assertEqual(response.status_code, status)
                self.assertEqual(self.state(), before)

    # -- who ---------------------------------------------------------------

    def test_a_visitor_is_refused_at_every_door(self):
        self.assert_refused(None, SIGNED_OUT)

    def test_a_member_is_refused_at_every_door(self):
        self.assert_refused(self.mia_user, 403)

    def test_a_senior_member_is_refused_at_every_door(self):
        self.assert_refused(self.sen_user, 403)

    def test_a_stranger_is_refused_at_every_door(self):
        self.assert_refused(self.stan_user, 403)

    def test_a_shareholder_is_refused_at_every_door(self):
        self.assert_refused(self.hold_user, 403)

    def test_staff_alone_is_refused_at_every_door(self):
        self.assert_refused(self.stef_user, 403)

    def test_another_companys_head_is_refused_at_every_door(self):
        self.assert_refused(self.other_head_user, 403)

    def test_a_superuser_without_the_plan_is_refused_at_every_door(self):
        from toto.socialhub.contact_access import is_administrator

        if is_administrator(self.late_user):
            self.skipTest("this host sells no Superuser plan: the privilege alone decides")
        self.assert_refused(self.late_user, 403)

    def test_the_head_passes_every_door(self):
        client = client_of(self.head_user)
        for name, url, body in self.doors():
            with self.subTest(door=name):
                self.assertEqual(client.post(url, body).status_code, 302)
        self.assertEqual(CompanyRecord.objects.get(community=self.acme).id_number,
                         "0000123456")
        self.assertEqual(
            dict(ShareHolding.objects.filter(community=self.acme)
                 .values_list("person__slug", "quantity")), {"mia": 5})

    def test_an_administrator_passes_every_door(self):
        client = client_of(self.root_user)
        for name, url, body in self.doors():
            with self.subTest(door=name):
                self.assertEqual(client.post(url, body).status_code, 302)
        self.assertEqual(CompanyRecord.objects.get(community=self.acme).id_number,
                         "0000123456")
        self.assertFalse(ShareHolding.objects.filter(pk=self.holding.pk).exists())

    # -- the method --------------------------------------------------------

    def test_every_door_takes_post_only(self):
        before = self.state()
        client = client_of(self.head_user)
        for name, url, body in self.doors():
            for method in ("get", "put", "delete", "patch"):
                with self.subTest(door=name, method=method):
                    response = getattr(client, method)(url)
                    self.assertEqual(response.status_code, 405)
                    self.assertEqual(response["Allow"], "POST")
        self.assertEqual(self.state(), before)

    # -- the address -------------------------------------------------------

    def test_an_ordinary_community_has_no_company_doors(self):
        for user in (self.head_user, self.root_user):
            self.assert_refused(user, 404, company=self.guild)

    def test_an_unknown_community_is_not_found(self):
        before = self.state()
        client = client_of(self.root_user)
        for url in ("/companies/no-such-company/number/",
                    "/companies/no-such-company/holdings/",
                    f"/companies/no-such-company/holdings/{self.holding.pk}/delete/"):
            self.assertEqual(client.post(url, {"id_number": "1", "person": self.mia.slug,
                                               "quantity": "1"}).status_code, 404)
        self.assertEqual(self.state(), before)

    def test_another_companys_holding_is_not_found_under_this_address(self):
        """The head of Acme names Globex's holding in Acme's address."""
        before = self.state()
        response = client_of(self.head_user).post(self.delete_url(self.acme, self.foreign.pk))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.state(), before)

    def test_an_administrator_too_deletes_only_within_the_company_named(self):
        before = self.state()
        response = client_of(self.root_user).post(self.delete_url(self.acme, self.foreign.pk))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.state(), before)

    def test_a_holding_that_is_not_there_is_not_found(self):
        response = client_of(self.head_user).post(self.delete_url(self.acme, 999999))
        self.assertEqual(response.status_code, 404)

    def test_a_community_that_stops_being_a_company_keeps_its_rows_behind_404(self):
        CompanyRecord.objects.create(community=self.acme, id_number="42")
        self.acme.org_type = "guild"
        self.acme.save()
        self.assert_refused(self.head_user, 404)
        self.assert_refused(self.root_user, 404)
        self.assertTrue(ShareHolding.objects.filter(pk=self.holding.pk).exists())
        self.acme.org_type = "company"
        self.acme.save()
        self.assertEqual(
            client_of(self.head_user).post(self.number_url(self.acme),
                                           {"id_number": "43"}).status_code, 302)
        self.assertEqual(CompanyRecord.objects.get(community=self.acme).id_number, "43")

    def test_every_door_carries_the_mark(self):
        from toto.companies import urls

        self.assertEqual(len(urls.urlpatterns), 3)
        for pattern in urls.urlpatterns:
            self.assertTrue(getattr(pattern.callback, "companies_door", False), pattern.name)


class IdNumberTests(CompaniesTestCase):
    def post(self, typed, user=None, company=None):
        company = company or self.acme
        return client_of(user or self.head_user).post(self.number_url(company),
                                                      {"id_number": typed})

    def number(self, company=None):
        record = CompanyRecord.objects.filter(community=company or self.acme).first()
        return record.id_number if record else None

    def test_leading_zeros_and_formatting_are_kept(self):
        for typed in ("0000123456", "000-12/3 A", "PL 0001", "KRS 0000012345"):
            with self.subTest(typed=typed):
                self.post(typed)
                self.assertEqual(self.number(), typed)

    def test_it_is_not_the_database_id_or_the_slug(self):
        self.post("0007")
        self.assertNotEqual(self.number(), str(self.acme.pk))
        self.assertNotEqual(self.number(), self.acme.slug)

    def test_spaces_at_its_ends_go_and_nothing_else(self):
        self.post("  00 12  ")
        self.assertEqual(self.number(), "00 12")

    def test_empty_clears_it(self):
        self.post("0007")
        self.post("")
        self.assertEqual(self.number(), "")
        self.assertEqual(CompanyRecord.objects.filter(community=self.acme).count(), 1)

    def test_too_long_is_refused_and_nothing_changes(self):
        self.post("0007")
        response = self.post("1" * 65)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.number(), "0007")
        self.post("1" * 64)
        self.assertEqual(self.number(), "1" * 64)

    def test_more_than_one_line_is_refused(self):
        for typed in ("12\n34", "12\x0034", "12\t34", "12‮34"):
            with self.subTest(typed=repr(typed)):
                self.post(typed)
                self.assertIsNone(self.number())

    def test_each_company_has_its_own(self):
        self.post("111")
        self.post("222", user=self.other_head_user, company=self.globex)
        self.assertEqual((self.number(), self.number(self.globex)), ("111", "222"))

    def test_who_saved_it_is_kept(self):
        self.post("111", user=self.root_user)
        self.assertEqual(CompanyRecord.objects.get(community=self.acme).updated_by,
                         self.root_user)
