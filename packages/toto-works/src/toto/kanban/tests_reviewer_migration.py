"""Retiring ``Task.reviewer``: the gate that replaced it.

``Task.reviewer`` no longer gates DONE; a mission's consensus policy does. The
conversion half of that change (the old ``0009_retire_task_reviewer``, which
moved reviewered tasks onto the "1 of 1" policy) went with every other
migration in the 2026-10-01 reset: a fresh database has nothing to convert.
The policies themselves are seeded by kanban's
``0002_seed_consensus_policies`` (and by ``ingress_kanban``), which is what
these tests read; ``tests_policy_seed`` checks the seed.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from toto.api.testutils import add_to_mesh
from toto.core.models import Platform
from toto.kanban import work
from toto.kanban.models import (
    Campaign, ConsensusPolicy, Mission, Practitioner, Project,
    ProjectCommitment, ReviewVerdict, Task, TaskStatus,
)
from toto.people.models import Person

User = get_user_model()


class GateReplacementTests(TestCase):
    """`Task.reviewer` no longer gates DONE; consensus does."""

    def setUp(self):
        Platform.objects.create(
            site_name="Test", author="t", publication_year=2026, active=True)
        self.user = add_to_mesh(
            User.objects.create_user(username="auditor", password="p"))
        self.person = Person.objects.create(
            user=self.user, display_name="A", email="a@x.com")
        practitioner = Practitioner.objects.create(
            person=self.person, role=Practitioner.ROLE_MANAGER)
        self.project = Project.objects.create(name="P", project_lead=self.person)
        ProjectCommitment.objects.create(
            practitioner=practitioner, project=self.project, hours_per_day=8)
        self.project.auditors.add(practitioner)
        self.campaign = Campaign.objects.create(project=self.project, name="C")

        other_user = add_to_mesh(
            User.objects.create_user(username="other", password="p"))
        other = Person.objects.create(
            user=other_user, display_name="O", email="o@x.com")
        self.other_reviewer = Practitioner.objects.create(
            person=other, role=Practitioner.ROLE_REVIEWER)
        ProjectCommitment.objects.create(
            practitioner=self.other_reviewer, project=self.project, hours_per_day=8)
        self.client.force_login(self.user)

    def _promote(self, task):
        return self.client.post(
            f"/kanban/{self.project.pk}/task/{task.pk}/promote/")

    def test_someone_elses_reviewer_no_longer_blocks_completion(self):
        """The retired rule. This used to be a hard 'only that reviewer' stop."""
        mission = Mission.objects.create(campaign=self.campaign, title="M")
        task = Task.objects.create(
            mission=mission, title="T", reviewer=self.other_reviewer)
        self._promote(task)
        self._promote(task)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.DONE)

    def test_a_policy_blocks_done_until_a_submission_is_accepted(self):
        policy = ConsensusPolicy.objects.get(name="1 of 1")
        mission = Mission.objects.create(
            campaign=self.campaign, title="M", consensus_policy=policy)
        task = Task.objects.create(mission=mission, title="T")

        self._promote(task)          # todo -> in_progress
        self._promote(task)          # blocked at the DONE edge
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        self.assertIsNotNone(work.done_blocked_reason(task))

        sub = work.submit(work.start_submission(task, self.person))
        work.record_review(sub, self.other_reviewer.person, ReviewVerdict.ACCEPT)
        work.resolve(sub)

        task.refresh_from_db()
        self.assertIsNone(work.done_blocked_reason(task))
        self._promote(task)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.DONE)

    def test_a_rejected_submission_does_not_unblock(self):
        policy = ConsensusPolicy.objects.get(name="1 of 1")
        mission = Mission.objects.create(
            campaign=self.campaign, title="M", consensus_policy=policy)
        task = Task.objects.create(mission=mission, title="T")
        sub = work.submit(work.start_submission(task, self.person))
        work.record_review(sub, self.other_reviewer.person, ReviewVerdict.REJECT)
        work.resolve(sub)
        self.assertIsNotNone(work.done_blocked_reason(task))

    def test_the_api_enforces_the_same_gate(self):
        """One helper, two surfaces — they must not drift."""
        policy = ConsensusPolicy.objects.get(name="1 of 1")
        mission = Mission.objects.create(
            campaign=self.campaign, title="M", consensus_policy=policy)
        task = Task.objects.create(
            mission=mission, title="T", status=TaskStatus.IN_PROGRESS)
        response = self.client.post(f"/kanban/api/tasks/{task.pk}/promote/")
        self.assertEqual(response.status_code, 403)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
