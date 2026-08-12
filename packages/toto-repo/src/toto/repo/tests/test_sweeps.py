"""The repo app's shared closer and its sweep policy."""

import datetime

from django.utils import timezone

from toto.repo import runner
from toto.repo.models import GitRun
from toto.quota import sweeps

from .base import RepoTestCase


class RepoSweepTests(RepoTestCase):
    def setUp(self):
        super().setUp()
        self.repo = self.make_repo()

    def _run(self, *, hours_ago, status=GitRun.RUNNING, stderr=""):
        return GitRun.objects.create(
            repo=self.repo, user=self.user, op="push", status=status,
            stderr=stderr,
            started_at=timezone.now() - datetime.timedelta(hours=hours_ago),
        )

    def test_fail_run_appends_and_closes(self):
        run = self._run(hours_ago=0, stderr="earlier output")

        runner.fail_run(run, "sweeper says goodbye")

        run.refresh_from_db()
        self.assertEqual(run.status, GitRun.FAILED)
        self.assertEqual(run.stderr, "earlier output\nsweeper says goodbye")
        self.assertIsNotNone(run.finished_at)

    def test_sweep_closes_the_stuck_and_spares_the_fresh(self):
        stuck = self._run(hours_ago=3)
        fresh = self._run(hours_ago=0)

        closed = sweeps.run_sweeps()

        self.assertEqual(closed.get("repo.GitRun"), 1)
        stuck.refresh_from_db()
        self.assertEqual(stuck.status, GitRun.FAILED)
        self.assertIn("stuck-run sweeper", stuck.stderr)
        fresh.refresh_from_db()
        self.assertEqual(fresh.status, GitRun.RUNNING)
