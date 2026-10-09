"""Tests for toto.monit (run from a host: manage.py test toto.monit)."""

import io
from datetime import timedelta

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform

from .models import Snapshot
from . import jobs, tasks
from .tasks import monit_prune, monit_sample


def _make_platform():
    return Platform.objects.create(
        site_name="Test", author="test", publication_year=2026, active=True)


def _on_the_plan(user):
    """monit's pages need a superuser on the Superuser plan (2026-10-01):
    `bootstrap_plans` puts every superuser on it — re-fetched, as it changes
    the rows under them."""
    if django_apps.is_installed("toto.subscriptions"):
        call_command("bootstrap_plans", stdout=io.StringIO())
    return type(user).objects.get(pk=user.pk)


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
        self.superuser = _on_the_plan(user_model.objects.create_superuser(
            "monit-admin", "monit@example.com", "pw"))
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


class JobSourceTests(SimpleTestCase):
    """The declarative run-table adapters.

    Every one of these is a promise about a model in ANOTHER package, made in
    strings so this one never imports it. Nothing checks that promise at import
    time — `_rows_for` swallows a FieldError and logs it — so a source whose
    field names are wrong shows an empty table forever and nobody notices. That
    is exactly how the forum's cleanup source came to be broken: it named a
    `created_at` its model never had. (That source left on 2026-10-09 with
    the parked forum; the lesson stays.) These tests are the check.
    """

    def test_every_source_is_declared_once(self):
        keys = [s.key for s in jobs.SOURCES]
        self.assertEqual(len(keys), len(set(keys)))

    def test_capsule_jobs_are_a_source(self):
        source = next(s for s in jobs.SOURCES if s.key == "capsule_job")
        self.assertEqual(source.model, "anastasia.Execution")
        # A STRING for apps.is_installed, never an import: toto-ops must not
        # gain a package edge on toto-anastasia, and a host without capsules
        # simply has no such source.
        self.assertEqual(source.app_label, "toto.anastasia")

    def test_no_source_names_a_field_its_model_does_not_have(self):
        """THE TEST THAT WOULD HAVE CAUGHT THE FORUM'S CLEANUP SOURCE (which
        is gone since 2026-10-09, with the parked forum).

        Installed sources only — a source for an app this host does not build
        is not a broken promise, it is an absent one.
        """
        from django.apps import apps as django_apps

        for source in jobs.SOURCES:
            if not django_apps.is_installed(source.app_label):
                continue
            with self.subTest(source=source.key):
                model = django_apps.get_model(source.model)
                names = {f.name for f in model._meta.get_fields()}
                declared = [source.status_field, source.started_field,
                            source.finished_field, source.created_field,
                            source.task_id_field, *source.error_fields,
                            *source.select_related]
                for field in [f for f in declared if f]:
                    self.assertIn(field, names,
                                  f"{source.key} names {field!r}")

    def test_a_killed_job_is_not_called_a_failure(self):
        """`killed` is a deadline or somebody's Cancel. The map already
        refuses to call `cancelled` a failure and this is the same case."""
        self.assertNotEqual(jobs._STATUS_MAP.get("killed"), jobs.FAILED)
        self.assertNotEqual(jobs._STATUS_MAP.get("lost"), jobs.FAILED)
