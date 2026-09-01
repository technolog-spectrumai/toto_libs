"""The merged Monitoring destination: the dispatcher and the strip.

Three tiles became one on 2026-09-01, and the two rules this file pins are the
ones that would rot silently:

* The DISPATCHER is the tile's one link, and it must send each role to the tab
  that role can actually open — a superuser to Monitoring, a staff-not-
  superuser to Audit. A tile that lands its own audience on a 403 is the
  tile-matches-gate failure with one hop added.
* The STRIP must hide what the viewer cannot open. monit's tabs are
  superuser-only and audit's is staff, and "staff" means is_staff OR
  is_superuser — the dashboard's reading, not staff_member_required's.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
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

    def test_a_plain_member_is_refused(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_anonymous_is_sent_to_log_in(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])


class StripTests(MonitoringTestCase):
    def test_a_superuser_sees_all_four_tabs_in_order(self):
        tabs = monitoring.monitoring_tabs(self.superuser)
        self.assertEqual([t["slug"] for t in tabs],
                         ["monitoring", "database", "audit", "history"])

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
