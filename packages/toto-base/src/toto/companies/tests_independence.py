"""Owning shares and being a member are separate things (stage 65), both
ways: leaving a community erases no share, and a share opens no door of the
community.

    manage.py test toto.companies.tests_independence
"""

from unittest import skipUnless

from django.apps import apps

from toto.companies import access
from toto.companies.models import ShareHolding
from toto.companies.register import holdings_of, register_of
from toto.companies.testing import CompaniesTestCase, client_of, page_url
from toto.socialhub.permissions import is_community_member, may_moderate_community


class LeavingKeepsTheSharesTests(CompaniesTestCase):
    def setUp(self):
        self.holding = self.hold_shares(self.acme, self.mia, 40)
        self.hold_shares(self.acme, self.hold, 60)

    def assert_mia_still_holds(self):
        self.holding.refresh_from_db()
        self.assertEqual(self.holding.quantity, 40)
        self.assertEqual(register_of(self.acme).total, 100)
        self.assertEqual({row.holding.person.slug for row in register_of(self.acme).rows},
                         {"mia", "hold"})
        self.assertEqual([row.quantity for row in holdings_of(self.mia, self.stan_user)], [40])

    def test_a_member_who_leaves_keeps_their_holding(self):
        self.assertTrue(is_community_member(self.mia_user, self.acme))
        self.mia.communities.remove(self.acme)
        self.assertFalse(is_community_member(self.mia_user, self.acme))
        self.assert_mia_still_holds()

    def test_removed_from_the_communitys_side(self):
        self.acme.members.remove(self.mia)
        self.assert_mia_still_holds()

    def test_leaving_every_community_at_once(self):
        self.mia.communities.clear()
        self.assert_mia_still_holds()

    def test_set_to_another_community(self):
        self.mia.communities.set([self.guild])
        self.assert_mia_still_holds()

    def test_a_senior_member_dropped_keeps_theirs(self):
        holding = self.hold_shares(self.acme, self.sen, 5)
        self.acme.senior_members.remove(self.sen)
        self.assertTrue(ShareHolding.objects.filter(pk=holding.pk).exists())

    def test_a_head_replaced_keeps_theirs(self):
        holding = self.hold_shares(self.acme, self.head, 5)
        self.acme.head = self.other_head
        self.acme.save()
        self.assertTrue(ShareHolding.objects.filter(pk=holding.pk).exists())
        self.assertFalse(access.may_manage(self.head_user, self.acme))

    def test_the_page_still_lists_who_left(self):
        self.mia.communities.remove(self.acme)
        response = client_of(self.stan_user).get(page_url(self.acme, "shareholdings"))
        self.assertContains(response, "/socialhub/profiles/mia/")
        self.assertContains(response, "40.00%")

    def test_joining_writes_no_holding(self):
        before = ShareHolding.objects.count()
        self.stan.communities.add(self.acme)
        self.stan.communities.add(self.globex)
        self.assertEqual(ShareHolding.objects.count(), before)
        self.assertEqual(holdings_of(self.stan, self.mia_user), [])


class SharesOpenNothingTests(CompaniesTestCase):
    def setUp(self):
        self.record(self.acme, self.hold, 1000)        # every recorded share
        self.record(self.acme, self.head, 0)

    def test_a_holder_is_not_made_a_member(self):
        self.assertFalse(self.hold.communities.filter(pk=self.acme.pk).exists())
        self.assertNotIn(self.hold, self.acme.members.all())
        self.assertFalse(is_community_member(self.hold_user, self.acme))

    def test_recording_through_the_door_adds_nobody_to_the_community(self):
        members = set(self.acme.members.values_list("slug", flat=True))
        self.record(self.acme, self.stan, 7)
        self.record(self.acme, self.stan, 9)
        self.assertEqual(set(self.acme.members.values_list("slug", flat=True)), members)
        self.assertFalse(self.acme.senior_members.filter(pk=self.stan.pk).exists())
        self.assertEqual(self.acme.head_id, self.head.pk)

    def test_holding_every_share_decides_nothing(self):
        self.assertFalse(may_moderate_community(self.hold_user, self.acme))
        self.assertFalse(access.may_manage(self.hold_user, self.acme))

    def test_the_head_with_no_share_still_manages(self):
        self.assertTrue(access.may_manage(self.head_user, self.acme))

    def test_the_holder_is_refused_at_every_door(self):
        holding = ShareHolding.objects.get(community=self.acme, person=self.hold)
        client = client_of(self.hold_user)
        for url, body in ((self.number_url(self.acme), {"id_number": "1"}),
                          (self.holding_url(self.acme),
                           {"person": self.hold.slug, "quantity": "2000"}),
                          (self.delete_url(self.acme, holding.pk), {})):
            self.assertEqual(client.post(url, body).status_code, 403)
        holding.refresh_from_db()
        self.assertEqual(holding.quantity, 1000)

    def test_the_holder_gets_no_form_on_the_page(self):
        response = client_of(self.hold_user).get(page_url(self.acme, "shareholdings"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="company-holding-form"')

    def test_removing_a_holding_removes_no_membership(self):
        holding = self.hold_shares(self.acme, self.mia, 3)
        client_of(self.head_user).post(self.delete_url(self.acme, holding.pk))
        self.assertTrue(is_community_member(self.mia_user, self.acme))

    def test_changing_a_holding_to_zero_changes_no_membership(self):
        self.record(self.acme, self.mia, 3)
        self.record(self.acme, self.mia, 0)
        self.assertTrue(is_community_member(self.mia_user, self.acme))
        self.assertFalse(is_community_member(self.hold_user, self.acme))

    def test_the_holder_is_not_listed_among_the_members(self):
        response = client_of(self.mia_user).get(page_url(self.acme))
        text = response.content.decode()
        members = text[text.index("People connected to this community."):]
        self.assertIn("/socialhub/profiles/mia/", members)
        self.assertNotIn("/socialhub/profiles/hold/", members)

    def test_the_app_writes_to_no_membership_table(self):
        """The companies code names no way into a community."""
        from pathlib import Path

        import toto.companies

        root = Path(toto.companies.__file__).parent
        for path in root.rglob("*.py"):
            if path.name.startswith("tests") or path.name == "testing.py":
                continue
            text = path.read_text(encoding="utf-8")
            for word in ("communities.add", "members.add", "senior_members", "communities.remove",
                         ".head =", "is_community_member"):
                self.assertNotIn(word, text, path.name)

    @skipUnless(apps.is_installed("toto.geography"), "no geography on this host")
    def test_the_holder_gets_nothing_of_the_communitys_map(self):
        from toto.geography.access import may_contribute, may_moderate

        self.assertFalse(may_contribute(self.hold_user, self.acme))
        self.assertFalse(may_moderate(self.hold_user, self.acme))
        self.assertTrue(may_contribute(self.mia_user, self.acme))


class TheChartIsNotTheRegisterTests(CompaniesTestCase):
    """Stage 66: the organisation chart is every community's and reads no
    holding; a person's position is what a manager set."""

    def setUp(self):
        from toto.socialhub import org_chart

        self.org_chart = org_chart
        self.record(self.acme, self.hold, 1000)
        self.record(self.acme, self.mia, 1)

    def titles(self):
        return {row.position.title: row.position.person
                for row in self.org_chart.chart_of(self.acme)}

    def test_holding_shares_puts_nobody_in_the_chart(self):
        self.assertEqual(self.org_chart.chart_of(self.acme), [])
        self.assertEqual(self.org_chart.nodes_of(self.acme), [])
        self.assertFalse(self.hold.community_positions.exists())

    def test_the_largest_holder_may_report_to_the_smallest(self):
        chief = self.org_chart.create(self.acme, title="Chief", person=self.mia)
        self.org_chart.create(self.acme, title="Clerk", person=self.hold,
                              reports_to=chief.pk)
        rows = [(row.position.title, row.position.person.slug, row.depth)
                for row in self.org_chart.chart_of(self.acme)]
        self.assertEqual(rows, [("Chief", "mia", 0), ("Clerk", "hold", 1)])

    def test_changing_a_holding_moves_no_position(self):
        chief = self.org_chart.create(self.acme, title="Chief", person=self.mia)
        self.org_chart.create(self.acme, title="Clerk", person=self.hold,
                              reports_to=chief.pk)
        before = self.titles()
        self.record(self.acme, self.mia, 0)
        self.record(self.acme, self.hold, 5_000_000)
        holding = ShareHolding.objects.get(community=self.acme, person=self.mia)
        client_of(self.head_user).post(self.delete_url(self.acme, holding.pk))
        self.assertEqual(self.titles(), before)

    def test_changing_the_chart_moves_no_share(self):
        before = dict(ShareHolding.objects.values_list("person__slug", "quantity"))
        chief = self.org_chart.create(self.acme, title="Chief", person=self.hold)
        self.org_chart.change(self.acme, chief.pk, title="Chief", person=None)
        self.org_chart.delete(self.acme, chief.pk)
        self.assertEqual(dict(ShareHolding.objects.values_list("person__slug", "quantity")),
                         before)

    def test_a_position_gives_no_say_over_the_register(self):
        self.org_chart.create(self.acme, title="Chief", person=self.stan)
        self.assertFalse(access.may_manage(self.stan_user, self.acme))
        self.assertEqual(self.record(self.acme, self.stan, 5, user=self.stan_user).status_code,
                         403)

    def test_an_ordinary_community_has_a_chart_and_no_register(self):
        self.org_chart.create(self.guild, title="Warden", person=self.head)
        response = client_of(self.mia_user).get(page_url(self.guild))
        keys = [tab["key"] for tab in response.context["community_tabs"]]
        self.assertIn("chart", keys)
        self.assertNotIn("shareholdings", keys)

    def test_the_two_codes_do_not_read_each_other(self):
        from pathlib import Path

        import toto.companies
        import toto.socialhub

        chart = (Path(toto.socialhub.__file__).parent / "org_chart.py").read_text("utf-8")
        for word in ("ShareHolding", "share_holdings", "toto.companies", "quantity"):
            self.assertNotIn(word, chart)
        for path in Path(toto.companies.__file__).parent.rglob("*.py"):
            if path.name.startswith("tests") or path.name == "testing.py":
                continue
            text = path.read_text(encoding="utf-8")
            for word in ("CommunityPosition", "org_chart", "community_positions"):
                self.assertNotIn(word, text, path.name)

