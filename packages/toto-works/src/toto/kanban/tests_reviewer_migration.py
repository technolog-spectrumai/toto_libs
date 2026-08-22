"""Retiring ``Task.reviewer``: the conversion, and the gate that replaced it.

Two halves of one change, tested together because what matters is that they
agree — a migration that seeds the right rows while the view still reads the
old column would pass a migration-shaped test and change nothing.

The migration function is called directly against the live app registry. Its
historical models are shape-identical to the current ones (it only reads
``Task.reviewer`` and writes rows this release added), so a real migration
runner buys nothing here and would need a dependency this suite does not have.
"""

import importlib

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from toto.api.testutils import add_to_mesh
from toto.core.models import Platform
from toto.kanban import work
from toto.kanban.models import (
    Campaign, ConsensusPolicy, Mission, Practitioner, Project,
    ProjectCommitment, ReviewVerdict, Task, TaskReviewer, TaskStatus,
)
from toto.people.models import Person

User = get_user_model()

_migration = importlib.import_module(
    "toto.kanban.migrations.0009_retire_task_reviewer")


def _run_migration():
    _migration.forwards(django_apps, None)


class ConversionTests(TestCase):
    def setUp(self):
        user = add_to_mesh(User.objects.create_user(username="lead", password="p"))
        self.person = Person.objects.create(
            user=user, display_name="Lead", email="l@x.com")
        self.project = Project.objects.create(name="P", project_lead=self.person)
        self.campaign = Campaign.objects.create(project=self.project, name="C")

        reviewer_user = add_to_mesh(
            User.objects.create_user(username="rev", password="p"))
        self.reviewer_person = Person.objects.create(
            user=reviewer_user, display_name="Rev", email="r@x.com")
        self.reviewer = Practitioner.objects.create(
            person=self.reviewer_person, role=Practitioner.ROLE_REVIEWER)

    def _mission(self, title):
        return Mission.objects.create(campaign=self.campaign, title=title)

    def test_it_seeds_the_canonical_policies(self):
        _run_migration()
        self.assertEqual(
            sorted(ConsensusPolicy.objects.values_list("name", flat=True)),
            ["1 of 1", "2 of 3", "3 of 5"])
        self.assertEqual(
            ConsensusPolicy.objects.filter(is_default=True).count(), 1)

    def test_a_reviewered_task_gives_its_mission_the_one_of_one_policy(self):
        mission = self._mission("gated")
        Task.objects.create(mission=mission, title="T", reviewer=self.reviewer)
        _run_migration()
        mission.refresh_from_db()
        self.assertEqual(mission.consensus_policy.name, "1 of 1")

    def test_the_old_reviewer_becomes_an_eligible_reviewer(self):
        mission = self._mission("gated")
        task = Task.objects.create(
            mission=mission, title="T", reviewer=self.reviewer)
        _run_migration()
        self.assertEqual(
            list(TaskReviewer.objects.filter(task=task)
                 .values_list("person_id", flat=True)),
            [self.reviewer_person.pk])

    def test_a_mission_without_reviewers_is_left_alone(self):
        """The whole point: boards that never used the gate gain no gate."""
        mission = self._mission("plain")
        Task.objects.create(mission=mission, title="T")
        _run_migration()
        mission.refresh_from_db()
        self.assertIsNone(mission.consensus_policy)
        self.assertIsNone(mission.effective_consensus_policy)

    def test_an_explicit_policy_is_not_overwritten(self):
        strict = ConsensusPolicy.objects.create(
            name="strict", required_reviews=5, required_accepts=3,
            reject_threshold=3)
        mission = self._mission("already chosen")
        mission.consensus_policy = strict
        mission.save(update_fields=["consensus_policy"])
        Task.objects.create(mission=mission, title="T", reviewer=self.reviewer)
        _run_migration()
        mission.refresh_from_db()
        self.assertEqual(mission.consensus_policy, strict)

    def test_running_it_twice_is_harmless(self):
        mission = self._mission("gated")
        Task.objects.create(mission=mission, title="T", reviewer=self.reviewer)
        _run_migration()
        _run_migration()
        self.assertEqual(ConsensusPolicy.objects.count(), 3)
        self.assertEqual(TaskReviewer.objects.count(), 1)

    def test_backwards_unwires_but_keeps_the_vocabulary(self):
        mission = self._mission("gated")
        Task.objects.create(mission=mission, title="T", reviewer=self.reviewer)
        _run_migration()
        _migration.backwards(django_apps, None)
        mission.refresh_from_db()
        self.assertIsNone(mission.consensus_policy)
        self.assertEqual(TaskReviewer.objects.count(), 0)
        # The rows themselves survive: other missions may point at them.
        self.assertEqual(ConsensusPolicy.objects.count(), 3)


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
