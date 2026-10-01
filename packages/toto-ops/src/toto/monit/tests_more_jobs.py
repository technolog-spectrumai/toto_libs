"""The Jobs page, the history helpers and the record checks, at their edges.

Named tests_more_jobs.py (sibling of tests.py) and meant for the gate's
host-owned block beside toto.monit.tests and toto.monit.tests_record. The run
rows come from toto.workflows, which every host that installs monit here also
installs; the other sources are asserted through the adapter alone.
"""

import os
import tempfile
import time
from collections import namedtuple
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform

from . import jobs, record, views
from .models import Snapshot

User = get_user_model()
DiskUsage = namedtuple("DiskUsage", "total used free")


def make_runs():
    from toto.workflows.models import Workflow, WorkflowRun

    workflow = Workflow.objects.create(name="Nightly")
    now = timezone.now()
    rows = {}
    for minutes, status in ((50, "pending"), (40, "running"), (30, "completed"),
                            (20, "failed"), (10, "cancelled")):
        run = WorkflowRun.objects.create(workflow=workflow, status=status)
        WorkflowRun.objects.filter(pk=run.pk).update(
            created_at=now - timedelta(minutes=minutes))
        rows[status] = run
    finished = rows["completed"]
    WorkflowRun.objects.filter(pk=finished.pk).update(
        started_at=now - timedelta(seconds=100), completed_at=now - timedelta(seconds=10))
    return rows


class RecentJobsTests(TestCase):
    def setUp(self):
        self.runs = make_runs()

    def test_each_apps_vocabulary_lands_on_the_canonical_five(self):
        rows = jobs.recent_jobs(source_key="workflow")
        by_raw = {r.raw_status: r.status for r in rows}
        self.assertEqual(by_raw, {"pending": "pending", "running": "running",
                                  "completed": "done", "failed": "failed",
                                  "cancelled": "other"})

    def test_newest_first_and_the_duration_is_measured(self):
        rows = jobs.recent_jobs(source_key="workflow")
        self.assertEqual([r.raw_status for r in rows],
                         ["cancelled", "failed", "completed", "running", "pending"])
        done = next(r for r in rows if r.raw_status == "completed")
        self.assertEqual(done.duration_s, 90.0)
        self.assertIsNone(next(r for r in rows if r.raw_status == "running").duration_s)
        self.assertEqual(done.source_label, "Workflow runs")
        self.assertIn("Nightly", done.subject)

    def test_a_status_filter_keeps_only_that_state(self):
        rows = jobs.recent_jobs(status=jobs.FAILED, source_key="workflow")
        self.assertEqual([r.pk for r in rows], [self.runs["failed"].pk])

    def test_the_limit_is_per_source(self):
        rows = jobs.recent_jobs(limit_per_source=2, source_key="workflow")
        self.assertEqual([r.raw_status for r in rows], ["cancelled", "failed"])

    def test_the_summary_counts_every_canonical_state(self):
        counts = jobs.summary(jobs.recent_jobs(source_key="workflow"))
        self.assertEqual(counts, {"pending": 1, "running": 1, "done": 1,
                                  "failed": 1, "other": 1})

    def test_node_runs_carry_their_task_id_and_error(self):
        from toto.workflows.models import WorkflowNode, WorkflowNodeRun

        run = self.runs["failed"]
        node = WorkflowNode.objects.create(workflow=run.workflow, node_type="join",
                                           label="merge")
        WorkflowNodeRun.objects.create(workflow_run=run, node=node, status="failed",
                                       error="x" * 900, celery_task_id="abc-1",
                                       started_at=timezone.now())

        (row,) = jobs.recent_jobs(source_key="workflow_node")

        self.assertEqual(row.task_id, "abc-1")
        self.assertEqual(len(row.error), 500)
        self.assertIsNotNone(row.created_at)  # falls back to started_at

    def test_one_broken_source_costs_only_its_own_rows(self):
        real = jobs._rows_for

        def flaky(source, limit):
            if source.key == "workflow_node":
                raise RuntimeError("mid-migration")
            return real(source, limit)

        with mock.patch.object(jobs, "_rows_for", side_effect=flaky), \
             self.assertLogs("toto.monit.jobs", level="WARNING"):
            rows = jobs.recent_jobs()

        self.assertEqual(len([r for r in rows if r.source == "workflow"]), 5)

    def test_an_unreadable_model_is_an_absent_source_not_an_error(self):
        source = jobs.JobSource(key="bad", label="Bad", app_label="toto.workflows",
                                model="workflows.WorkflowRun",
                                created_field="no_such_field")
        with self.assertLogs("toto.monit.jobs", level="DEBUG"):
            self.assertEqual(jobs._rows_for(source, 10), [])

    def test_an_uninstalled_app_is_never_queried(self):
        source = next(s for s in jobs.SOURCES if s.key == "ingestion")
        self.assertEqual(jobs._rows_for(source, 10), [])
        self.assertNotIn(source, jobs.available_sources())

    def test_first_error_takes_the_first_non_empty_field(self):
        obj = SimpleNamespace(error="", detail="the detail", stderr="later")
        self.assertEqual(jobs._first_error(obj, ("error", "detail", "stderr")),
                         "the detail")
        self.assertEqual(jobs._first_error(obj, ()), "")


class JobsPageTests(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="T", author="T", publication_year=2026,
                                active=True)
        self.root = User.objects.create_superuser("root", "r@x.test", "pw")
        self.client.force_login(self.root)
        make_runs()
        patcher = mock.patch("toto.celery_utils.celery_available", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_staff_member_who_is_not_superuser_is_refused(self):
        self.client.force_login(User.objects.create_user("staff", password="pw",
                                                         is_staff=True))
        self.assertEqual(self.client.get(reverse("monit:jobs")).status_code, 403)

    def test_filters_narrow_the_list(self):
        response = self.client.get(reverse("monit:jobs"),
                                   {"status": "FAILED", "source": "workflow"})
        self.assertEqual(response.context["active_status"], "failed")
        self.assertEqual(response.context["active_source"], "workflow")
        self.assertEqual([j.raw_status for j in response.context["jobs"]], ["failed"])

    def test_unknown_filter_values_are_ignored_not_trusted(self):
        response = self.client.get(reverse("monit:jobs"),
                                   {"status": "status__in", "source": "ocr"})
        self.assertEqual(response.context["active_status"], "")
        self.assertEqual(response.context["active_source"], "")
        self.assertEqual(len(response.context["jobs"]), 5)
        self.assertFalse(response.context["truncated"])
        self.assertIs(response.context["celery_ok"], False)


class HistoryHelperTests(SimpleTestCase):
    def snap(self, minutes, **values):
        base = datetime(2026, 9, 1, 12, 0, tzinfo=dt_timezone.utc)
        defaults = {"web_process_start": None, "web_requests_total": None}
        defaults.update(values)
        return SimpleNamespace(created=base + timedelta(minutes=minutes), **defaults)

    def test_short_series_are_not_downsampled(self):
        snaps = [self.snap(i) for i in range(5)]
        self.assertIs(views._downsample(snaps, max_points=5), snaps)

    def test_long_series_are_strided_and_keep_the_newest_point(self):
        snaps = [self.snap(i) for i in range(10)]
        sampled = views._downsample(snaps, max_points=4)
        self.assertEqual([s.created.minute for s in sampled], [0, 3, 6, 9])
        odd = views._downsample([self.snap(i) for i in range(11)], max_points=4)
        self.assertEqual(odd[-1].created.minute, 10)

    @override_settings(TIME_ZONE="UTC")
    def test_labels_add_the_date_only_when_the_window_spans_days(self):
        self.assertEqual(views._labels([]), [])
        same_day = views._labels([self.snap(0), self.snap(30)])
        self.assertEqual(same_day, ["12:00", "12:30"])
        two_days = views._labels([self.snap(0), self.snap(24 * 60)])
        self.assertEqual(two_days, ["01.09 12:00", "02.09 12:00"])

    def test_series_keep_gaps_and_scale(self):
        snaps = [self.snap(0, v=1048576), self.snap(1, v=None)]
        self.assertEqual(views._series(snaps, "v", 1 / 1048576, 1), [1.0, None])

    def test_rates_pair_only_snapshots_of_the_same_worker(self):
        snaps = [
            self.snap(0, web_process_start=1.0, web_requests_total=100),
            self.snap(2, web_process_start=1.0, web_requests_total=160),
            self.snap(4, web_process_start=2.0, web_requests_total=5),   # restart
            self.snap(6, web_process_start=2.0, web_requests_total=1),   # counter reset
            self.snap(8, web_process_start=2.0, web_requests_total=None),
        ]
        self.assertEqual(views._per_worker_rates(snaps, "web_requests_total"),
                         [None, 30.0, None, 0.0, None])

    def test_the_legend_shows_only_for_several_series(self):
        import json

        one = json.loads(views._chart(["a"], [views._dataset("x", [1], views.BLUE)]))
        two = json.loads(views._chart(["a"], [
            views._dataset("x", [1], views.BLUE),
            views._dataset("y", [2], views.GRAY, dashed=True)], y_title="ms"))
        self.assertFalse(one["options"]["plugins"]["legend"]["display"])
        self.assertTrue(two["options"]["plugins"]["legend"]["display"])
        self.assertEqual(two["datasets"][1]["borderDash"], [6, 4])
        self.assertEqual(two["options"]["scales"]["y"]["title"]["text"], "ms")
        self.assertNotIn("borderDash", one["datasets"][0])


class MonitPagesTests(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="T", author="T", publication_year=2026,
                                active=True)
        self.client.force_login(User.objects.create_superuser("root", "r@x.test", "pw"))

    def test_health_says_error_when_the_database_does_not_answer(self):
        import json

        from django.test import RequestFactory

        request = RequestFactory().get("/monit/health/")
        with mock.patch.object(connection, "cursor", side_effect=RuntimeError("down")):
            response = views.HealthView.as_view()(request)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.content), {"status": "error"})
        self.assertEqual(response["Cache-Control"], "no-store")

    @override_settings(MONIT_MONGO_HOST="mongo", MONIT_LAKEFS_URL="http://lakefs/_health")
    def test_the_overview_probes_boards_and_store_only_when_configured(self):
        with mock.patch("toto.monit.collectors.collect_celery",
                        return_value={"celery_ok": True, "celery_workers": 1}), \
             mock.patch("toto.monit.collectors.collect_system",
                        return_value={}), \
             mock.patch("toto.monit.collectors._tcp_probe",
                        return_value=(True, 2.0)), \
             mock.patch("toto.monit.collectors._http_probe",
                        return_value=(False, None)):
            context = self.client.get(reverse("monit:overview")).context

        self.assertTrue(context["boards_configured"])
        self.assertTrue(context["store_configured"])
        self.assertIs(context["live_boards"]["mongo_ok"], True)
        self.assertIs(context["live_store"]["lakefs_ok"], False)
        self.assertIsNone(context["live_redis"])

    def test_the_overview_without_extras_asks_nothing_of_them(self):
        with mock.patch("toto.monit.collectors.collect_celery", return_value={}), \
             mock.patch("toto.monit.collectors.collect_system", return_value={}), \
             mock.patch("toto.monit.collectors.collect_boards") as boards:
            context = self.client.get(reverse("monit:overview")).context
        boards.assert_not_called()
        self.assertIsNone(context["live_boards"])
        self.assertIsNone(context["live_store"])

    @override_settings(MONIT_REDIS_URL="redis://redis.invalid/0",
                       MONIT_WEB_METRICS_URL="http://web/metrics")
    def test_history_counts_worker_restarts_and_charts_redis_when_configured(self):
        now = timezone.now()
        for i, start in enumerate((1.0, 1.0, 2.0, 3.0)):
            Snapshot.objects.create(created=now - timedelta(minutes=10 - i),
                                    web_process_start=start, db_latency_ms=1.0,
                                    redis_latency_ms=0.5)
        Snapshot.objects.create(created=now - timedelta(hours=72))  # outside the window

        context = self.client.get(reverse("monit:history")).context

        self.assertEqual(context["snapshot_count"], 4)
        self.assertEqual(context["web_restarts"], 2)
        self.assertIn("Redis (ms)", context["latency_chart_json"])
        self.assertIn("Web worker RSS (MB)", context["mem_chart_json"])

    def test_history_with_no_snapshots_draws_nothing(self):
        context = self.client.get(reverse("monit:history")).context
        self.assertFalse(context["has_history"])
        self.assertNotIn("cpu_chart_json", context)


class RecordFormattingTests(SimpleTestCase):
    def test_bytes_are_humanised(self):
        self.assertEqual(record._bytes(None), "—")
        self.assertEqual(record._bytes(512), "512 B")
        self.assertEqual(record._bytes(1536), "1.5 KB")
        self.assertEqual(record._bytes(3 * 1024 ** 3), "3.0 GB")
        self.assertEqual(record._bytes(2 * 1024 ** 5), "2.0 PB")

    def test_ages_are_humanised(self):
        self.assertEqual(record._age(None), "—")
        self.assertEqual(record._age(30), "30s ago")
        self.assertEqual(record._age(600), "10 min ago")
        self.assertEqual(record._age(5 * 3600), "5 h ago")
        self.assertEqual(record._age(72 * 3600), "3 days ago")

    def test_only_warn_and_fail_are_bad(self):
        mk = lambda status: record.Check("k", "K", status, "s")
        self.assertEqual([mk(s).is_bad for s in (record.OK, record.WARN, record.FAIL,
                                                  record.UNKNOWN, record.OFF)],
                         [False, True, True, False, False])

    def test_worst_of_all_ok_or_off_is_ok_and_unknown_outranks_them(self):
        mk = lambda status: record.Check("k", "K", status, "s")
        self.assertEqual(record.worst([mk(record.OK), mk(record.OFF)]), record.OK)
        self.assertEqual(record.worst([mk(record.OFF), mk(record.UNKNOWN)]),
                         record.UNKNOWN)
        self.assertEqual(record.worst([]), record.OK)


class RecordCheckTests(TestCase):
    def test_disk_thresholds(self):
        def at(percent):
            usage = DiskUsage(total=100, used=percent, free=100 - percent)
            with mock.patch.object(record.shutil, "disk_usage", return_value=usage):
                return record.check_disk()

        self.assertEqual(at(50).status, record.OK)
        self.assertEqual(at(record.DISK_WARN_PERCENT).status, record.WARN)
        self.assertEqual(at(record.DISK_FAIL_PERCENT).status, record.FAIL)
        self.assertEqual(at(90).value, "90%")

    def test_a_slow_database_is_a_warning(self):
        with mock.patch.object(record.time, "perf_counter", side_effect=[0.0, 0.3]):
            check = record.check_database()
        self.assertEqual(check.status, record.WARN)
        self.assertEqual(check.value, "300.0 ms")

    def test_unapplied_migrations_fail_and_are_named(self):
        plan = [(SimpleNamespace(app_label="workflows", name="0009_new"), False)]
        with mock.patch("django.db.migrations.executor.MigrationExecutor.migration_plan",
                        return_value=plan):
            check = record.check_migrations()
        self.assertEqual(check.status, record.FAIL)
        self.assertEqual(check.summary, "1 not applied.")
        self.assertEqual(check.detail, "workflows.0009_new")

    def test_media_counts_files_and_refuses_a_read_only_store(self):
        with tempfile.TemporaryDirectory() as scratch:
            Path(scratch, "a.txt").write_bytes(b"x" * 10)
            Path(scratch, "sub").mkdir()
            Path(scratch, "sub", "b.txt").write_bytes(b"y" * 20)
            with override_settings(MEDIA_ROOT=scratch):
                ok = record.check_media()
                with mock.patch.object(record, "_writable", return_value=False):
                    read_only = record.check_media()

        self.assertEqual((ok.status, ok.value), (record.OK, "2"))
        self.assertIn("30 B", ok.summary)
        self.assertEqual(read_only.status, record.FAIL)
        self.assertIn("Not writable", read_only.summary)

    def test_backups_name_a_missing_directory_and_ignore_symlinks(self):
        with tempfile.TemporaryDirectory() as scratch:
            real = Path(scratch, "db")
            real.mkdir()
            dump = real / "dump.sql.gz"
            dump.write_bytes(b"x")
            old = time.time() - (record.BACKUP_STALE_HOURS + 1) * 3600
            os.utime(dump, (old, old))
            (real / "latest").symlink_to(dump)
            gone = Path(scratch, "media-backups")
            with override_settings(MONIT_BACKUP_DIRS=[str(real), str(gone)]):
                check = record.check_backups()

        self.assertEqual(check.status, record.FAIL)  # the symlink did not freshen it
        self.assertIn("dump.sql.gz", check.detail)
        self.assertIn(f"missing: {gone}", check.detail)

    def test_a_broken_audit_chain_fails_and_says_where(self):
        broken = SimpleNamespace(ok=False, checked=10, first_bad_sequence=7,
                                 detail="hash mismatch")
        with mock.patch("toto.audit.services.verify_chain", return_value=broken):
            check = record.check_audit()
        self.assertEqual(check.status, record.FAIL)
        self.assertEqual(check.summary, "Broken at sequence 7.")
        self.assertEqual(check.detail, "hash mismatch")

    def test_run_checks_answers_every_check_even_when_one_explodes(self):
        with mock.patch.object(record.shutil, "disk_usage", side_effect=OSError("gone")):
            checks = record.run_checks()
        self.assertEqual([c.key for c in checks],
                         ["database", "migrations", "media", "disk", "backups", "audit",
                          "certificate", "overdue", "mail"])
        disk = next(c for c in checks if c.key == "disk")
        self.assertEqual(disk.status, record.UNKNOWN)
        self.assertIn("OSError", disk.detail)
