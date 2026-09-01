"""Tests for toto.monit (run from a host: manage.py test toto.monit)."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform

from .models import Snapshot
from . import tasks
from .tasks import monit_prune, monit_sample


def _make_platform():
    return Platform.objects.create(
        site_name="Test", author="test", publication_year=2026, active=True)


class HealthViewTests(TestCase):
    def test_health_is_public_and_ok(self):
        response = self.client.get(reverse("monit:health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(response["Cache-Control"], "no-store")


class OverviewViewTests(TestCase):
    def setUp(self):
        _make_platform()
        user_model = get_user_model()
        self.superuser = user_model.objects.create_superuser(
            "monit-admin", "monit@example.com", "pw")
        self.plain = user_model.objects.create_user(
            "monit-user", "user@example.com", "pw")

    def test_anonymous_forbidden(self):
        # 403 from MonitAccessMixin, or 302 where the HOST refuses anonymous
        # traffic before the view is reached (zenobia's
        # LoginRequiredEverywhereMiddleware). Refused either way, and which
        # mechanism gets there first is the deployment's business — the same
        # pair tests_record.py accepts for the Database page.
        self.assertIn(self.client.get(reverse("monit:overview")).status_code,
                      (302, 403))

    def test_non_superuser_forbidden(self):
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get(reverse("monit:overview")).status_code, 403)

    def test_superuser_renders(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("monit:overview"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Monitoring")

    def test_the_overview_no_longer_draws_history(self):
        """The six charts moved to the History tab on 2026-09-01. Asserted from
        this side too: a page that still names a chart id would be one that
        kept the section and the Chart.js load with it."""
        self.client.force_login(self.superuser)
        for _ in range(3):
            Snapshot.objects.create(sys_cpu_percent=1.0, db_ok=True)
        body = self.client.get(reverse("monit:overview")).content.decode()
        self.assertNotIn("monit_cpu", body)
        self.assertNotIn("chart.umd.min.js", body)

    def test_the_overview_offers_the_tab_strip(self):
        self.client.force_login(self.superuser)
        tabs = self.client.get(reverse("monit:overview")).context["monitoring_tabs"]
        self.assertEqual([t["slug"] for t in tabs if t["active"]], ["monitoring"])


class HistoryViewTests(OverviewViewTests):
    """The 48 h charts, on their own tab since 2026-09-01.

    Subclasses OverviewViewTests for its fixture, and re-runs its permission
    tests against this URL — the History tab carries the same superuser gate as
    the rest of monit, and a tab that quietly relaxed it would hand the machine
    trends to anyone with an account.
    """

    def test_anonymous_forbidden(self):
        self.assertIn(self.client.get(reverse("monit:history")).status_code,
                      (302, 403))

    def test_non_superuser_forbidden(self):
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get(reverse("monit:history")).status_code, 403)

    def test_superuser_renders(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("monit:history"))
        self.assertEqual(response.status_code, 200)

    def test_the_overview_no_longer_draws_history(self):
        """Inherited name, opposite meaning here — skip the parent's version."""

    def test_the_overview_offers_the_tab_strip(self):
        self.client.force_login(self.superuser)
        tabs = self.client.get(reverse("monit:history")).context["monitoring_tabs"]
        self.assertEqual([t["slug"] for t in tabs if t["active"]], ["history"])

    def test_superuser_renders_with_history(self):
        now = timezone.now()
        for i in range(5):
            Snapshot.objects.create(
                created=now - timedelta(minutes=2 * i),
                sys_cpu_percent=10.0 + i, db_ok=True, db_latency_ms=1.5,
                web_process_start=1000.0, web_requests_total=100 * (5 - i),
                web_responses_5xx=i)
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("monit:history"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "monit_cpu")

    def test_it_loads_the_chart_library_the_partial_needs(self):
        """oya/partials/chart.html calls dim() at parse time and constructs the
        Chart itself, so the PAGE must load chart.umd.min.js and include
        dim.html. Both moved here with the section; without either the charts
        draw nothing and say nothing."""
        Snapshot.objects.create(sys_cpu_percent=1.0)
        self.client.force_login(self.superuser)
        body = self.client.get(reverse("monit:history")).content.decode()
        self.assertIn("chart.umd.min.js", body)
        self.assertIn("function dim(", body)

    @override_settings(MONIT_WEB_METRICS_URL="http://web:8000/metrics")
    def test_rate_chart_shown_only_with_web_scrape(self):
        Snapshot.objects.create(sys_cpu_percent=1.0)
        self.client.force_login(self.superuser)
        self.assertContains(self.client.get(reverse("monit:history")), 'id="monit_rate"')

    def test_rate_chart_hidden_without_web_scrape(self):
        Snapshot.objects.create(sys_cpu_percent=1.0)
        self.client.force_login(self.superuser)
        self.assertNotContains(self.client.get(reverse("monit:history")), 'id="monit_rate"')


class TaskTests(TestCase):
    def test_sample_creates_snapshot(self):
        monit_sample()
        self.assertEqual(Snapshot.objects.count(), 1)
        snapshot = Snapshot.objects.get()
        # DB is definitely reachable inside the test — collector must agree.
        self.assertTrue(snapshot.db_ok)

    def test_prune_respects_retention(self):
        old = Snapshot.objects.create()
        Snapshot.objects.filter(pk=old.pk).update(
            created=timezone.now() - timedelta(hours=72))
        fresh = Snapshot.objects.create()
        monit_prune()
        remaining = list(Snapshot.objects.values_list("pk", flat=True))
        self.assertEqual(remaining, [fresh.pk])


class TaskRegistrationTests(SimpleTestCase):
    """Beat schedules these; the worker has to be able to find them."""

    def test_the_worker_discovers_monit_tasks(self):
        # Without the registry entry, beat enqueued monit_prune once an hour and
        # the worker answered KeyError every time — a stack trace that looks
        # like a broken worker and buries the ones that matter.
        from toto.registry import TASK_MODULES

        self.assertIn("toto.monit", TASK_MODULES)

    def test_every_scheduled_monit_task_exists(self):
        from toto import schedules

        scheduled = {entry["task"] for entry in
                     schedules.beat_schedule(monit=True).values()
                     if entry["task"].startswith("toto.monit.")}
        self.assertTrue(scheduled, "nothing scheduled to check")
        for name in scheduled:
            with self.subTest(task=name):
                self.assertTrue(hasattr(tasks, name.rsplit(".", 1)[1]),
                                f"{name} is scheduled but not defined")
