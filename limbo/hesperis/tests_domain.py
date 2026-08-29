"""AcceptedObservation and DatasetVersion: the two guarantees Hesperis owns.

1. An observation exists ONLY because consensus accepted a submission.
2. A dataset version is a release, not a live query.

Both are asserted structurally where possible — a OneToOne, a PROTECT, a
save() that refuses — because a guarantee that depends on everyone calling the
right function is a convention, not a guarantee.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from toto.api.testutils import add_to_mesh
from toto.kanban import work
from toto.kanban.models import (
    Campaign, ConsensusPolicy, Mission, Practitioner, Project,
    ProjectCommitment, ReviewVerdict, Task,
)
from toto.people.models import Person
from toto.hesperis import services
from toto.hesperis.models import (
    AcceptedObservation, Dataset, DatasetVersion, DatasetVersionMember,
    HesperisBounty, HesperisCampaign,
)

User = get_user_model()


def _person(username):
    user = add_to_mesh(User.objects.create_user(username=username, password="p"))
    return user, Person.objects.create(
        user=user, display_name=username.title(), email=f"{username}@x.com")


class HesperisTestBase(TestCase):
    def setUp(self):
        _u, self.author = _person("author")
        self.project = Project.objects.create(name="P", project_lead=self.author)
        self.campaign = Campaign.objects.create(project=self.project, name="C")
        self.pcampaign = HesperisCampaign.objects.create(campaign=self.campaign)
        self.rule = ConsensusPolicy.objects.get(name="1 of 1")
        self.mission = Mission.objects.create(
            campaign=self.campaign, title="Photograph the bridges",
            consensus_policy=self.rule)
        self.bounty = HesperisBounty.objects.create(
            mission=self.mission, instructions="One photo per bridge.")

    def _reviewer(self, username="rev"):
        user, person = _person(username)
        practitioner = Practitioner.objects.create(
            person=person, role=Practitioner.ROLE_REVIEWER)
        ProjectCommitment.objects.create(
            practitioner=practitioner, project=self.project, hours_per_day=8)
        return person

    def _contribute_and_resolve(self, verdict=ReviewVerdict.ACCEPT, who=None):
        person = who or self.author
        submission = services.contribute(self.bounty, person)
        work.submit(submission)
        work.record_review(submission, self._reviewer(f"rev-{person.pk}"), verdict)
        with self.captureOnCommitCallbacks(execute=True):
            work.resolve(submission)
        submission.refresh_from_db()
        return submission


class ContributionTests(HesperisTestBase):
    def test_contributing_creates_this_persons_own_task(self):
        """One bounty, many contributions — without a second task system."""
        submission = services.contribute(self.bounty, self.author)
        self.assertEqual(Task.objects.filter(mission=self.mission).count(), 1)
        self.assertEqual(submission.task.mission, self.mission)
        self.assertTrue(submission.task.assignments.filter(
            person=self.author, released_at__isnull=True).exists())

    def test_two_contributors_get_two_tasks(self):
        _u, other = _person("other")
        services.contribute(self.bounty, self.author)
        services.contribute(self.bounty, other)
        self.assertEqual(Task.objects.filter(mission=self.mission).count(), 2)

    def test_contributing_twice_reuses_the_open_draft(self):
        first = services.contribute(self.bounty, self.author)
        second = services.contribute(self.bounty, self.author)
        self.assertEqual(first.pk, second.pk)

    def test_a_closed_bounty_refuses_contributions(self):
        self.bounty.closes_at = timezone.now() - timezone.timedelta(days=1)
        self.bounty.save(update_fields=["closes_at"])
        with self.assertRaises(ValidationError):
            services.contribute(self.bounty, self.author)

    def test_a_closed_campaign_closes_its_bounties(self):
        self.pcampaign.is_open = False
        self.pcampaign.save(update_fields=["is_open"])
        self.assertFalse(self.bounty.is_open())


class AcceptanceTests(HesperisTestBase):
    def test_an_accepted_submission_becomes_an_observation(self):
        submission = self._contribute_and_resolve()
        observation = services.accept(submission)
        self.assertEqual(observation.submission, submission)
        self.assertEqual(observation.bounty, self.bounty)
        self.assertEqual(observation.observed_by, self.author)

    def test_a_pending_submission_cannot(self):
        """The guarantee: nothing unreviewed reaches the dataset."""
        submission = services.contribute(self.bounty, self.author)
        work.submit(submission)
        with self.assertRaises(ValidationError):
            services.accept(submission)
        self.assertEqual(AcceptedObservation.objects.count(), 0)

    def test_a_rejected_submission_cannot(self):
        submission = self._contribute_and_resolve(ReviewVerdict.REJECT)
        with self.assertRaises(ValidationError):
            services.accept(submission)

    def test_a_draft_cannot(self):
        submission = services.contribute(self.bounty, self.author)
        with self.assertRaises(ValidationError):
            services.accept(submission)

    def test_a_hand_poked_resolution_without_consensus_cannot(self):
        """`resolved_at` is the evidence that work.resolve wrote the outcome."""
        from toto.kanban.models import Submission, SubmissionResolution

        submission = services.contribute(self.bounty, self.author)
        work.submit(submission)
        # The CheckConstraint forbids accepted-without-timestamp, so the only
        # way to fake this is in memory — which is exactly what accept() must
        # not trust.
        submission.resolution = SubmissionResolution.ACCEPTED
        submission.resolved_at = None
        with self.assertRaises(ValidationError):
            services.accept(submission)

    def test_accepting_twice_yields_one_observation(self):
        submission = self._contribute_and_resolve()
        first = services.accept(submission)
        second = services.accept(submission)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(AcceptedObservation.objects.count(), 1)

    def test_a_submission_outside_a_bounty_cannot_become_one(self):
        plain_mission = Mission.objects.create(
            campaign=self.campaign, title="ordinary", consensus_policy=self.rule)
        task = Task.objects.create(mission=plain_mission, title="T")
        submission = work.submit(work.start_submission(task, self.author))
        work.record_review(submission, self._reviewer("r2"), ReviewVerdict.ACCEPT)
        work.resolve(submission)
        submission.refresh_from_db()
        with self.assertRaises(ValidationError):
            services.accept(submission)

    def test_the_sweep_is_safe_to_re_run(self):
        self._contribute_and_resolve()
        _u, other = _person("other")
        self._contribute_and_resolve(who=other)
        self.assertEqual(len(services.accept_all_pending(self.bounty)), 2)
        services.accept_all_pending(self.bounty)
        self.assertEqual(AcceptedObservation.objects.count(), 2)

    def test_the_raw_submission_is_untouched_by_acceptance(self):
        """A claim and a fact are different rows; one never becomes the other."""
        submission = self._contribute_and_resolve()
        services.accept(submission)
        submission.refresh_from_db()
        self.assertIsNotNone(submission.pk)
        self.assertEqual(submission.task.mission, self.mission)


class DatasetVersionTests(HesperisTestBase):
    def setUp(self):
        super().setUp()
        self.dataset = Dataset.objects.create(
            campaign=self.pcampaign, name="Bridges", slug="bridges")

    def _accept_one(self, username):
        _u, person = _person(username)
        submission = self._contribute_and_resolve(who=person)
        return services.accept(submission)

    def test_freezing_copies_membership_and_stamps_a_hash(self):
        self._accept_one("a1")
        self._accept_one("a2")
        version = services.freeze(self.dataset, by=self.author)
        self.assertEqual(version.number, 1)
        self.assertEqual(version.members.count(), 2)
        self.assertTrue(version.manifest_hash)
        self.assertTrue(version.verify())

    def test_a_release_does_not_grow_when_the_campaign_does(self):
        """The whole difference between a release and a live query."""
        self._accept_one("a1")
        version = services.freeze(self.dataset)
        self._accept_one("a2")
        self.assertEqual(version.members.count(), 1)
        self.assertEqual(self.dataset.live_observation_count(), 2)

    def test_a_second_version_captures_the_newer_state(self):
        self._accept_one("a1")
        first = services.freeze(self.dataset)
        self._accept_one("a2")
        second = services.freeze(self.dataset)
        self.assertEqual(second.number, 2)
        self.assertEqual(first.members.count(), 1)
        self.assertEqual(second.members.count(), 2)
        self.assertNotEqual(first.manifest_hash, second.manifest_hash)

    def test_a_frozen_version_refuses_a_new_member(self):
        self._accept_one("a1")
        version = services.freeze(self.dataset)
        stray = self._accept_one("a2")
        with self.assertRaises(ValidationError):
            DatasetVersionMember.objects.create(
                version=version, observation=stray)

    def test_membership_cannot_be_edited(self):
        self._accept_one("a1")
        version = services.freeze(self.dataset)
        member = version.members.first()
        with self.assertRaises(ValidationError):
            member.save()

    def test_verify_notices_a_removed_row(self):
        """What the manifest hash is FOR: proving a release is unchanged."""
        self._accept_one("a1")
        self._accept_one("a2")
        version = services.freeze(self.dataset)
        self.assertTrue(version.verify())
        version.members.first().delete()
        self.assertFalse(version.verify())

    def test_an_observation_in_a_release_cannot_be_deleted(self):
        from django.db.models import ProtectedError

        observation = self._accept_one("a1")
        services.freeze(self.dataset)
        with self.assertRaises(ProtectedError):
            observation.delete()

    def test_freezing_an_empty_dataset_is_allowed_and_verifiable(self):
        version = services.freeze(self.dataset)
        self.assertEqual(version.members.count(), 0)
        self.assertTrue(version.verify())
