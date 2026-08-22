"""Optional rewards, on a host with NO economy installed.

That last part is the point of running these under
``toto.kanban.testing.settings``: it installs no ``toto.assets``, so this suite
proves the seam degrades rather than proving the ledger works. A reward system
that only holds together where the ledger exists would be a hard dependency
wearing a plugin's clothes.

``captureOnCommitCallbacks`` is load-bearing throughout: settlement is deferred
to ``transaction.on_commit``, and ``TestCase`` wraps each test in a transaction
that never commits — so without it the callbacks silently never run and every
assertion about settlement would pass for the wrong reason.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from toto.api.testutils import add_to_mesh
from toto.kanban import rewards, work
from toto.kanban.models import (
    Campaign, ConsensusPolicy, Mission, Practitioner, Project,
    ProjectCommitment, ReviewVerdict, RewardGrant, RewardGrantState,
    RewardPolicy, RewardTrigger, SubmissionResolution, Task,
)
from toto.people.models import Person

User = get_user_model()

#: Records what it was asked to settle, so a test can assert "once".
SETTLED: list[str] = []


class RecordingBackend(rewards.RewardBackend):
    def settle(self, grant):
        SETTLED.append(grant.reference)
        rewards._finish(grant, RewardGrantState.SETTLED, "recorded")


class ExplodingBackend(rewards.RewardBackend):
    def settle(self, grant):
        raise RuntimeError("the ledger is on fire")


def _person(username):
    user = add_to_mesh(User.objects.create_user(username=username, password="p"))
    return user, Person.objects.create(
        user=user, display_name=username.title(), email=f"{username}@x.com")


class RewardTestBase(TestCase):
    def setUp(self):
        SETTLED.clear()
        _u, self.author = _person("author")
        self.project = Project.objects.create(name="P", project_lead=self.author)
        self.campaign = Campaign.objects.create(project=self.project, name="C")
        self.policy_rule = ConsensusPolicy.objects.get(name="1 of 1")
        self.mission = Mission.objects.create(
            campaign=self.campaign, title="M", consensus_policy=self.policy_rule)
        self.task = Task.objects.create(mission=self.mission, title="T")

    def _reviewer(self, username):
        user, person = _person(username)
        practitioner = Practitioner.objects.create(
            person=person, role=Practitioner.ROLE_REVIEWER)
        ProjectCommitment.objects.create(
            practitioner=practitioner, project=self.project, hours_per_day=8)
        return person

    def _reward(self, trigger, amount=5, **kw):
        return RewardPolicy.objects.create(
            mission=self.mission, trigger=trigger, asset_code="GEM",
            amount_base_units=amount, funding_account_code="test_purse", **kw)

    def _run(self, verdicts=(ReviewVerdict.ACCEPT,)):
        """Submit, review, resolve — with on_commit callbacks actually fired."""
        submission = work.submit(work.start_submission(self.task, self.author))
        for i, verdict in enumerate(verdicts):
            work.record_review(submission, self._reviewer(f"rev{i}"), verdict)
        with self.captureOnCommitCallbacks(execute=True):
            work.resolve(submission)
        submission.refresh_from_db()
        return submission


class NoRewardTests(RewardTestBase):
    """Ordinary kanban: no policy, therefore nothing at all."""

    def test_a_board_with_no_reward_policy_records_nothing(self):
        submission = self._run()
        self.assertEqual(submission.resolution, SubmissionResolution.ACCEPTED)
        self.assertEqual(RewardGrant.objects.count(), 0)


class NullBackendTests(RewardTestBase):
    """The default backend, which is what every economy-less host gets."""

    def test_a_grant_is_recorded_and_skipped(self):
        self._reward(RewardTrigger.SUBMISSION_ACCEPTED)
        self._run()
        grant = RewardGrant.objects.get()
        self.assertEqual(grant.state, RewardGrantState.SKIPPED)
        self.assertEqual(grant.asset_code, "GEM")
        self.assertEqual(grant.amount_base_units, 5)
        self.assertEqual(grant.recipient, self.author)

    def test_the_intent_survives_for_a_host_that_adopts_a_backend_later(self):
        self._reward(RewardTrigger.SUBMISSION_ACCEPTED)
        self._run()
        self.assertTrue(
            RewardGrant.objects.filter(state=RewardGrantState.SKIPPED).exists())


@override_settings(
    KANBAN_REWARD_BACKEND="toto.kanban.tests_rewards.RecordingBackend")
class SettlementTests(RewardTestBase):
    def test_an_accepted_submission_pays_its_author_once(self):
        self._reward(RewardTrigger.SUBMISSION_ACCEPTED)
        self._run()
        self.assertEqual(len(SETTLED), 1)
        grant = RewardGrant.objects.get()
        self.assertEqual(grant.state, RewardGrantState.SETTLED)
        self.assertEqual(grant.recipient, self.author)

    def test_a_rejected_submission_pays_nothing(self):
        self._reward(RewardTrigger.SUBMISSION_ACCEPTED)
        submission = self._run([ReviewVerdict.REJECT])
        self.assertEqual(submission.resolution, SubmissionResolution.REJECTED)
        self.assertEqual(RewardGrant.objects.count(), 0)
        self.assertEqual(SETTLED, [])

    def test_resolving_twice_pays_once(self):
        """The property the whole reference scheme exists for."""
        self._reward(RewardTrigger.SUBMISSION_ACCEPTED)
        submission = self._run()
        with self.captureOnCommitCallbacks(execute=True):
            work.resolve(submission)
        self.assertEqual(RewardGrant.objects.count(), 1)
        self.assertEqual(len(SETTLED), 1)

    def test_reviewers_are_paid_whichever_way_they_voted(self):
        """Paying only the majority would pay people to agree."""
        policy = ConsensusPolicy.objects.get(name="2 of 3")
        self.mission.consensus_policy = policy
        self.mission.save(update_fields=["consensus_policy"])
        self._reward(RewardTrigger.REVIEW_RESOLVED, amount=1)
        self._run([ReviewVerdict.ACCEPT, ReviewVerdict.REJECT,
                   ReviewVerdict.ACCEPT])
        self.assertEqual(RewardGrant.objects.count(), 3)
        self.assertEqual(len(SETTLED), 3)
        self.assertEqual(
            set(RewardGrant.objects.values_list("state", flat=True)),
            {RewardGrantState.SETTLED})

    def test_both_triggers_can_pay_for_one_resolution(self):
        self._reward(RewardTrigger.SUBMISSION_ACCEPTED, amount=10)
        self._reward(RewardTrigger.REVIEW_RESOLVED, amount=1)
        self._run()
        self.assertEqual(RewardGrant.objects.count(), 2)
        self.assertEqual(
            sorted(RewardGrant.objects.values_list("amount_base_units", flat=True)),
            [1, 10])

    def test_an_inactive_policy_pays_nothing(self):
        self._reward(RewardTrigger.SUBMISSION_ACCEPTED, active=False)
        self._run()
        self.assertEqual(RewardGrant.objects.count(), 0)

    def test_a_campaign_policy_reaches_its_missions(self):
        RewardPolicy.objects.create(
            campaign=self.campaign, trigger=RewardTrigger.SUBMISSION_ACCEPTED,
            asset_code="GEM", amount_base_units=7,
            funding_account_code="test_purse")
        self._run()
        self.assertEqual(RewardGrant.objects.get().amount_base_units, 7)

    def test_raising_the_amount_afterwards_does_not_pay_again(self):
        policy = self._reward(RewardTrigger.SUBMISSION_ACCEPTED)
        submission = self._run()
        policy.amount_base_units = 9999
        policy.save(update_fields=["amount_base_units"])
        with self.captureOnCommitCallbacks(execute=True):
            work.resolve(submission)
        self.assertEqual(RewardGrant.objects.count(), 1)
        self.assertEqual(RewardGrant.objects.get().amount_base_units, 5)


@override_settings(
    KANBAN_REWARD_BACKEND="toto.kanban.tests_rewards.ExplodingBackend")
class FailureTests(RewardTestBase):
    def test_a_broken_ledger_does_not_undo_the_acceptance(self):
        """An exhausted purse takes this path, and must cost nothing but the reward."""
        self._reward(RewardTrigger.SUBMISSION_ACCEPTED)
        with self.assertRaises(RuntimeError):
            self._run()
        submission = self.task.submissions.get()
        self.assertEqual(submission.resolution, SubmissionResolution.ACCEPTED)
        self.assertIsNotNone(submission.resolved_at)
        self.assertEqual(RewardGrant.objects.count(), 1)

    def test_a_failed_grant_can_be_retried_safely(self):
        self._reward(RewardTrigger.SUBMISSION_ACCEPTED)
        try:
            self._run()
        except RuntimeError:
            pass
        RewardGrant.objects.all().update(
            state=RewardGrantState.FAILED, detail="boom")
        with override_settings(
                KANBAN_REWARD_BACKEND="toto.kanban.tests_rewards.RecordingBackend"):
            count = rewards.retry_failed()
        self.assertEqual(count, 1)
        self.assertEqual(RewardGrant.objects.get().state, RewardGrantState.SETTLED)
        self.assertEqual(len(SETTLED), 1)


class ReferenceTests(RewardTestBase):
    def test_the_same_payment_computes_the_same_reference(self):
        """Stable references are the entire basis of the idempotency."""
        policy = self._reward(RewardTrigger.SUBMISSION_ACCEPTED)
        submission = work.submit(work.start_submission(self.task, self.author))
        first = rewards.reference_for(policy, self.author, submission=submission)
        second = rewards.reference_for(policy, self.author, submission=submission)
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("kanban:reward:"))

    def test_recording_the_same_grant_twice_returns_one_row(self):
        policy = self._reward(RewardTrigger.SUBMISSION_ACCEPTED)
        submission = work.submit(work.start_submission(self.task, self.author))
        a = rewards.record_grant(policy, self.author, submission=submission)
        b = rewards.record_grant(policy, self.author, submission=submission)
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(RewardGrant.objects.count(), 1)
