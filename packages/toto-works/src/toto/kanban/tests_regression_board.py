"""The ordinary board, unchanged by the work engine.

Every model the engine added is nullable or defaulted, and every gate it added
is off unless a mission names a ``ConsensusPolicy``. That is a claim about
behaviour, not about schema, so it is asserted here rather than assumed: a
board that uses none of the engine must promote, demote, count and complete
exactly as it did before any of it existed.

This suite is the one that should fail loudly if the engine ever starts
applying itself by default.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from toto.api.testutils import add_to_mesh
from toto.core.models import Platform
from toto.kanban import work
from toto.kanban.metrics import summarize_tasks
from toto.kanban.models import (
    Campaign, ConsensusPolicy, Mission, Practitioner, Project,
    ProjectCommitment, ReviewVerdict, Task, TaskStatus,
)
from toto.people.models import Person

User = get_user_model()


class PlainBoardTests(TestCase):
    """No ConsensusPolicy anywhere — the state every pre-existing board is in."""

    def setUp(self):
        # The page views decorate their context through PageProcessor, which
        # needs a Platform row; without one they 404 rather than render.
        Platform.objects.create(
            site_name="Test", author="t", publication_year=2026, active=True)
        self.user = add_to_mesh(User.objects.create_user(
            username="auditor", password="pass"))
        self.person = Person.objects.create(
            user=self.user, display_name="Auditor", email="a@x.com")
        self.practitioner = Practitioner.objects.create(
            person=self.person, role=Practitioner.ROLE_MANAGER)
        self.project = Project.objects.create(
            name="P", project_lead=self.person)
        ProjectCommitment.objects.create(
            practitioner=self.practitioner, project=self.project, hours_per_day=8)
        self.project.auditors.add(self.practitioner)
        self.campaign = Campaign.objects.create(project=self.project, name="C")
        self.mission = Mission.objects.create(campaign=self.campaign, title="M")
        self.task = Task.objects.create(mission=self.mission, title="T")
        self.client.force_login(self.user)

    def _promote(self):
        return self.client.post(
            f"/kanban/{self.project.pk}/task/{self.task.pk}/promote/")

    def _demote(self):
        return self.client.post(
            f"/kanban/{self.project.pk}/task/{self.task.pk}/demote/")

    def test_a_mission_has_no_review_gate_by_default(self):
        self.assertIsNone(self.mission.effective_consensus_policy)

    def test_a_default_policy_row_does_not_apply_itself(self):
        """A global default must not switch the engine on everywhere.

        There IS a default row — the migration seeds one — and this is the test
        that stops `effective_consensus_policy` from quietly reaching for it.
        """
        # The default row already exists: migration 0009 seeds "1 of 1" as
        # the default. That is precisely the row that must not apply itself.
        self.assertTrue(ConsensusPolicy.objects.filter(is_default=True).exists())
        self.mission.refresh_from_db()
        self.assertIsNone(self.mission.effective_consensus_policy)

    def test_promote_to_done_still_works(self):
        self._promote()
        self._promote()
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.DONE)
        self.assertIsNotNone(self.task.completed_at)

    def test_demote_out_of_done_still_clears_the_timestamp(self):
        self._promote()
        self._promote()
        self._demote()
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.IN_PROGRESS)
        self.assertIsNone(self.task.completed_at)

    def test_a_task_carrying_submissions_still_promotes_without_a_policy(self):
        """Submissions alone must not gate a board that never asked for review."""
        work.submit(work.start_submission(self.task, self.person, notes="wip"))
        self._promote()
        self._promote()
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, TaskStatus.DONE)

    def test_the_board_page_renders_with_engine_rows_present(self):
        """A smoke test with real rows: the board must not query what it lacks."""
        work.open_assignment(self.task, self.person)
        work.submit(work.start_submission(self.task, self.person))
        response = self.client.get(f"/kanban/project/{self.project.pk}/")
        self.assertEqual(response.status_code, 200)

    def test_mission_page_renders_with_engine_rows_present(self):
        work.submit(work.start_submission(self.task, self.person))
        response = self.client.get(f"/kanban/mission/{self.mission.pk}/")
        self.assertEqual(response.status_code, 200)

    def test_metrics_ignore_submissions(self):
        """Counts key on Task, and the engine adds no Task rows."""
        before = summarize_tasks([self.task])
        work.submit(work.start_submission(self.task, self.person))
        self.task.refresh_from_db()
        after = summarize_tasks([self.task])
        self.assertEqual(before, after)


class EngineOptInTests(TestCase):
    """The engine engages only where a mission opts in."""

    def test_a_policy_on_one_mission_does_not_reach_its_sibling(self):
        _u = add_to_mesh(User.objects.create_user(username="lead", password="pass"))
        person = Person.objects.create(
            user=_u, display_name="Lead", email="l@x.com")
        project = Project.objects.create(name="P", project_lead=person)
        campaign = Campaign.objects.create(project=project, name="C")
        policy = ConsensusPolicy.objects.get(name="1 of 1")
        gated = Mission.objects.create(
            campaign=campaign, title="gated", consensus_policy=policy)
        plain = Mission.objects.create(campaign=campaign, title="plain")

        self.assertEqual(gated.effective_consensus_policy, policy)
        self.assertIsNone(plain.effective_consensus_policy)

    def test_a_campaign_policy_reaches_missions_that_name_none(self):
        _u = add_to_mesh(User.objects.create_user(username="lead2", password="pass"))
        person = Person.objects.create(
            user=_u, display_name="Lead", email="l2@x.com")
        project = Project.objects.create(name="P", project_lead=person)
        policy = ConsensusPolicy.objects.get(name="2 of 3")
        campaign = Campaign.objects.create(
            project=project, name="C", consensus_policy=policy)
        mission = Mission.objects.create(campaign=campaign, title="M")
        self.assertEqual(mission.effective_consensus_policy, policy)

    def test_a_mission_policy_overrides_its_campaign(self):
        _u = add_to_mesh(User.objects.create_user(username="lead3", password="pass"))
        person = Person.objects.create(
            user=_u, display_name="Lead", email="l3@x.com")
        project = Project.objects.create(name="P", project_lead=person)
        loose = ConsensusPolicy.objects.create(
            name="loose", required_reviews=1, required_accepts=1, reject_threshold=1)
        strict = ConsensusPolicy.objects.create(
            name="strict", required_reviews=5, required_accepts=3, reject_threshold=3)
        campaign = Campaign.objects.create(
            project=project, name="C", consensus_policy=loose)
        mission = Mission.objects.create(
            campaign=campaign, title="M", consensus_policy=strict)
        self.assertEqual(mission.effective_consensus_policy, strict)
