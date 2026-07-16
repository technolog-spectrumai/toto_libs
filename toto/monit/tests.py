"""Tests for toto.monit (run from a host: manage.py test toto.monit)."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform

from .models import Snapshot
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
        self.assertEqual(self.client.get(reverse("monit:overview")).status_code, 403)

    def test_non_superuser_forbidden(self):
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get(reverse("monit:overview")).status_code, 403)

    def test_superuser_renders(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("monit:overview"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Monitoring")

    def test_superuser_renders_with_history(self):
        now = timezone.now()
        for i in range(5):
            Snapshot.objects.create(
                created=now - timedelta(minutes=2 * i),
                sys_cpu_percent=10.0 + i, db_ok=True, db_latency_ms=1.5,
                web_process_start=1000.0, web_requests_total=100 * (5 - i),
                web_responses_5xx=i)
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("monit:overview"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "monit_cpu")

    @override_settings(MONIT_WEB_METRICS_URL="http://web:8000/metrics")
    def test_rate_chart_shown_only_with_web_scrape(self):
        Snapshot.objects.create(sys_cpu_percent=1.0)
        self.client.force_login(self.superuser)
        self.assertContains(self.client.get(reverse("monit:overview")), 'id="monit_rate"')

    def test_rate_chart_hidden_without_web_scrape(self):
        Snapshot.objects.create(sys_cpu_percent=1.0)
        self.client.force_login(self.superuser)
        self.assertNotContains(self.client.get(reverse("monit:overview")), 'id="monit_rate"')


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
