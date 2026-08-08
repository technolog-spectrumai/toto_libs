"""FileJob's new lifecycle facts: timestamps, task ids, budgets, the closer.

Named tests_lifecycle.py (sibling of tests.py) and listed explicitly in the
gate — `toto` is a namespace package, discovery finds nothing by itself.
"""

import datetime
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from toto.quota import sweeps, times

from .models import FileJob, fail_job

User = get_user_model()


def make_job(owner, *, status=FileJob.Status.PENDING, hours_ago=0, **kwargs):
    job = FileJob.objects.create(
        name="job", command="probe", owner=owner, status=status, **kwargs)
    if hours_ago:
        FileJob.objects.filter(pk=job.pk).update(
            created_at=timezone.now() - datetime.timedelta(hours=hours_ago))
        job.refresh_from_db()
    return job


class LifecycleTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("alice", password="pw")

    def test_fail_job_records_reason_and_finished_at(self):
        job = make_job(self.owner, status=FileJob.Status.RUNNING)

        fail_job(job, "swept away")

        job.refresh_from_db()
        self.assertEqual(job.status, FileJob.Status.FAILED)
        self.assertEqual(job.output["error"], "swept away")
        self.assertIsNotNone(job.finished_at)

    def test_duration_property(self):
        job = make_job(self.owner)
        self.assertIsNone(job.duration)
        job.started_at = timezone.now()
        job.finished_at = job.started_at + datetime.timedelta(seconds=90)
        self.assertEqual(job.duration, 90.0)

    def test_task_captures_celery_task_id(self):
        from . import tasks_direct

        job = make_job(self.owner)
        with patch.object(tasks_direct, "get_command") as get_command:
            get_command.return_value = MagicMock()
            tasks_direct.run_direct_job.apply(args=[job.pk],
                                              task_id="task-abc-123")

        job.refresh_from_db()
        self.assertEqual(job.celery_task_id, "task-abc-123")

    def test_sweep_closes_old_pending_jobs(self):
        stuck = make_job(self.owner, hours_ago=6)
        fresh = make_job(self.owner)

        closed = sweeps.run_sweeps()

        self.assertEqual(closed.get("manta.FileJob"), 1)
        stuck.refresh_from_db()
        self.assertEqual(stuck.status, FileJob.Status.FAILED)
        self.assertIn("stuck-run sweeper", stuck.output["error"])
        fresh.refresh_from_db()
        self.assertEqual(fresh.status, FileJob.Status.PENDING)


class BudgetInvariantTests(TestCase):
    """The lockstep contracts as executable facts."""

    def test_manta_ceiling_fits_under_the_visibility_timeout(self):
        decl = times.registry.get("manta.job_runtime")
        self.assertIsNotNone(decl)
        visibility = settings.CELERY_BROKER_TRANSPORT_OPTIONS["visibility_timeout"]
        self.assertLess(decl.ceiling_seconds + 100, visibility)

    def test_sweep_floor_exceeds_the_largest_grantable_runtime(self):
        decl = times.registry.get("manta.job_runtime")
        policy = next(p for p in sweeps.all_policies()
                      if p.model_label == "manta.FileJob")
        self.assertGreaterEqual(policy.cutoff_seconds,
                                decl.ceiling_seconds + 100 + 1800)
