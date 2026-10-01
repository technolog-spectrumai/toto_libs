"""The heartbeats on the pages (2026-10-01): the Jobs page's schedule table,
FaucetRun and the run records in its list, and the overdue check on the
Database page — through the full middleware stack."""

from __future__ import annotations

import io
from datetime import timedelta
from unittest import mock

from celery.schedules import crontab
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform
from toto.monit.models import BeatEntry, TaskRun

User = get_user_model()

SCHEDULE = {
    "tax-daily-levy": {"task": "toto.tax.tasks.run_daily_levy",
                       "schedule": crontab(hour=4, minute=15)},
    "subscriptions-monthly-billing": {"task": "toto.subscriptions.tasks.run_billing",
                                      "schedule": crontab(hour=5, minute=5)},
    "monit-sample": {"task": "toto.monit.tasks.monit_sample",
                     "schedule": crontab(minute="*/2")},
}


@override_settings(CELERY_BEAT_SCHEDULE=SCHEDULE)
class HeartbeatPagesTests(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="T", author="T", publication_year=2026,
                                active=True)
        root = User.objects.create_superuser("root", "r@x.test", "pw")
        # The pages need the Superuser plan too (2026-10-01); bootstrap_plans
        # puts every superuser on it.
        if django_apps.is_installed("toto.subscriptions"):
            call_command("bootstrap_plans", stdout=io.StringIO())
        self.client.force_login(User.objects.get(pk=root.pk))
        patcher = mock.patch("toto.celery_utils.celery_available", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        now = timezone.now()
        TaskRun.objects.create(
            task="toto.tax.tasks.run_daily_levy", task_id="levy-1", status="success",
            started_at=now - timedelta(hours=2), finished_at=now - timedelta(hours=2),
            summary="metric_code=storage, day=2026-10-01, counts={levied=3}")
        TaskRun.objects.create(
            task="toto.subscriptions.tasks.run_billing", task_id="bill-1",
            status="failed", started_at=now - timedelta(minutes=5),
            finished_at=now - timedelta(minutes=5), error="ValueError: no wallet")
        BeatEntry.objects.create(name="monit-sample", task="toto.monit.tasks.monit_sample",
                                 first_seen=now - timedelta(days=2),
                                 last_seen=now - timedelta(days=2))

    def test_the_jobs_page_shows_each_entry_its_last_run_and_its_verdict(self):
        response = self.client.get(reverse("monit:jobs"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="schedule-table"')
        self.assertContains(response, 'data-testid="schedule-cards"')
        self.assertContains(response, "metric_code=storage, day=2026-10-01, counts={levied=3}")
        self.assertContains(response, "ValueError: no wallet")
        self.assertContains(response, "On time")
        # Never started, scheduled two days ago, every two minutes.
        self.assertContains(response, "Long overdue")
        self.assertContains(response, "Celery beat last started")
        names = [entry.name for entry in response.context["schedule"]]
        self.assertEqual(names, sorted(SCHEDULE))

    def test_the_run_records_and_faucet_runs_are_in_the_list(self):
        if django_apps.is_installed("toto.assets"):
            from toto.assets.models import FaucetRun

            FaucetRun.objects.create(period_label="2026-10-01T04", paid=2,
                                     finished_at=timezone.now())
        response = self.client.get(reverse("monit:jobs"))
        sources = {job.source for job in response.context["jobs"]}
        self.assertIn("beat", sources)
        if django_apps.is_installed("toto.assets"):
            self.assertIn("faucet", sources)
            self.assertContains(response, "2026-10-01T04: 2 paid, 0 skipped, 0 failed")
        only = self.client.get(reverse("monit:jobs"), {"source": "beat", "status": "failed"})
        self.assertEqual([job.task_id for job in only.context["jobs"]], ["bill-1"])

    def test_the_database_page_carries_the_overdue_check(self):
        response = self.client.get(reverse("monit:status"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Scheduled tasks")
        self.assertContains(response, "1 of 3 scheduled tasks is overdue: monit-sample.")
