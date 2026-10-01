"""The merged Monitoring destination: the dispatcher and the strip.

Three tiles became one on 2026-09-01, and the two rules this file pins are the
ones that would rot silently:

* The DISPATCHER is the tile's one link, and it must send each role to the tab
  that role can actually open — a superuser to Monitoring, a staff-not-
  superuser to Audit. A tile that lands its own audience on a 403 is the
  tile-matches-gate failure with one hop added.
* The STRIP must hide what the viewer cannot open. monit's tabs are for a
  superuser on the Superuser plan (2026-10-01) and audit's is staff, and
  "staff" means is_staff OR is_superuser — the dashboard's reading, not
  staff_member_required's.
"""
from __future__ import annotations

from io import StringIO
from unittest import mock, skipUnless

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from toto.core import monitoring
from toto.core.models import Platform

User = get_user_model()


class MonitoringTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="Test", author="t",
                                publication_year=2026, active=True)
        cls.superuser = User.objects.create_superuser("root", password="x")
        cls.staff = User.objects.create_user("clerk", password="x",
                                             is_staff=True)
        cls.member = User.objects.create_user("member", password="x")
        # monit's tabs need the Superuser plan too (2026-10-01):
        # bootstrap_plans puts every superuser on it — re-fetched, as it
        # changes the rows under them.
        if apps.is_installed("toto.subscriptions"):
            call_command("bootstrap_plans", stdout=StringIO())
        cls.superuser = User.objects.get(pk=cls.superuser.pk)
        # Made after the bootstrap: a superuser who never took the plan.
        cls.bare_root = User.objects.create_superuser("bare-root", password="x")


class DispatcherTests(MonitoringTestCase):
    def url(self):
        return reverse("core:monitoring")

    def test_superuser_lands_on_the_monitoring_tab(self):
        self.client.force_login(self.superuser)
        response = self.client.get(self.url())
        self.assertRedirects(response, reverse("monit:overview"),
                             fetch_redirect_response=False)

    def test_staff_lands_on_the_audit_tab(self):
        """The one tab a staff-not-superuser may open. Sending them to
        monit:overview would land the tile's own audience on a 403."""
        self.client.force_login(self.staff)
        response = self.client.get(self.url())
        self.assertRedirects(response, reverse("audit:index"),
                             fetch_redirect_response=False)

    @skipUnless(apps.is_installed("toto.subscriptions"), "no plan sold here")
    def test_a_superuser_without_the_plan_lands_on_the_audit_tab(self):
        """Monitoring answers them 403 since 2026-10-01; Audit, a staff tab,
        is theirs — the superuser bit counts as staff there."""
        self.client.force_login(self.bare_root)
        response = self.client.get(self.url())
        self.assertRedirects(response, reverse("audit:index"),
                             fetch_redirect_response=False)

    def test_a_plain_member_is_refused(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_anonymous_is_sent_to_log_in(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])


class StripTests(MonitoringTestCase):
    def test_a_superuser_sees_every_tab_in_order(self):
        """"Jobs" joined on 2026-09-06, between Audit and History.

        The exact list rather than a count, and it earned that: adding a tab
        failed this test loudly, which is what an ORDERED strip wants — a
        count would have passed while the new tab sat in the wrong place.
        """
        tabs = monitoring.monitoring_tabs(self.superuser)
        self.assertEqual([t["slug"] for t in tabs],
                         ["monitoring", "database", "audit", "jobs", "history"])

    def test_staff_sees_only_the_audit_tab(self):
        tabs = monitoring.monitoring_tabs(self.staff)
        self.assertEqual([t["slug"] for t in tabs], ["audit"])

    def test_a_superuser_without_the_staff_bit_keeps_the_audit_tab(self):
        """is_superuser does not imply is_staff in Django. The strip reads
        "staff" the way the dashboard does — is_staff OR is_superuser — so a
        superuser minted without the staff bit must not lose Audit."""
        bare = User.objects.create_user("bare", password="x",
                                        is_superuser=True, is_staff=False)
        self.assertIn("audit",
                      [t["slug"] for t in monitoring.monitoring_tabs(bare)])

    def test_a_member_sees_nothing(self):
        self.assertEqual(monitoring.monitoring_tabs(self.member), [])

    @skipUnless(apps.is_installed("toto.subscriptions"), "no plan sold here")
    def test_a_superuser_without_the_plan_sees_only_the_audit_tab(self):
        """monit's four tabs would refuse them (2026-10-01); the strip hides
        what its doors refuse."""
        self.assertEqual([t["slug"] for t in monitoring.monitoring_tabs(self.bare_root)],
                         ["audit"])


class SuperuserOnPlanTests(MonitoringTestCase):
    """monit's one question (2026-10-01): a superuser on the Superuser plan —
    both, never one."""

    def test_only_a_superuser_on_the_plan(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertTrue(monitoring.superuser_on_plan(self.superuser))
        for user in (AnonymousUser(), self.member, self.staff):
            with self.subTest(user=str(user)):
                self.assertFalse(monitoring.superuser_on_plan(user))

    @skipUnless(apps.is_installed("toto.subscriptions"), "no plan sold here")
    def test_the_superuser_bit_alone_is_not_enough(self):
        self.assertFalse(monitoring.superuser_on_plan(self.bare_root))

    def test_a_host_that_sells_no_plan_asks_for_the_bit_alone(self):
        real = apps.is_installed

        def without_plans(label):
            return False if label == "toto.subscriptions" else real(label)

        with mock.patch.object(apps, "is_installed", side_effect=without_plans):
            self.assertTrue(monitoring.superuser_on_plan(self.bare_root))
            self.assertFalse(monitoring.superuser_on_plan(self.staff))

    def test_exactly_one_tab_is_active(self):
        tabs = monitoring.monitoring_tabs(self.superuser, active="database")
        self.assertEqual([t["slug"] for t in tabs if t["active"]], ["database"])

    def test_an_unmounted_tab_is_dropped_not_broken(self):
        """reverse() is tried per tab and NoReverseMatch drops that tab alone —
        a host that installs monit but mounts nothing renders a shorter strip
        rather than 500ing every page that includes it."""
        from unittest import mock

        with mock.patch("django.urls.reverse", side_effect=NoReverseMatch):
            self.assertEqual(monitoring.monitoring_tabs(self.superuser), [])
