"""The work engine: assignment, submission immutability, review, consensus.

These are grouped by INVARIANT rather than by model, because what matters is
that the pieces agree — a submission that freezes but a review that can be
recorded against it anyway would pass two model-shaped suites and still be
broken.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from toto.api.testutils import add_to_mesh
from toto.kanban import work
from toto.kanban.models import (
    Assignment, Campaign, ConsensusPolicy, Mission, Practitioner, Project,
    ProjectCommitment, Review, ReviewVerdict, Submission, SubmissionResolution,
    SubmissionState, Task, TaskReviewer,
)
from toto.people.models import Person

User = get_user_model()


def _person(username, *, staff=False):
    user = add_to_mesh(User.objects.create_user(
        username=username, password="pass", is_staff=staff))
    return user, Person.objects.create(
        user=user, display_name=username.title(), email=f"{username}@x.com")


def _world(policy=None):
    """A project whose lead is `owner`, with one mission and one task."""
    owner_user, owner = _person("owner")
    project = Project.objects.create(name="P", project_lead=owner)
    campaign = Campaign.objects.create(project=project, name="C")
    mission = Mission.objects.create(
        campaign=campaign, title="M", consensus_policy=policy)
    task = Task.objects.create(mission=mission, title="T")
    return owner_user, owner, project, campaign, mission, task


def _reviewer(project, username):
    """An active reviewer committed to the project — the fallback eligibility."""
    user, person = _person(username)
    practitioner = Practitioner.objects.create(
        person=person, role=Practitioner.ROLE_REVIEWER)
    ProjectCommitment.objects.create(
        practitioner=practitioner, project=project, hours_per_day=8)
    return user, person


def _policy(name="1 of 1", reviews=1, accepts=1, rejects=1, changes=None):
    """Get-or-shape a policy by name.

    update_or_create, not create: migration 0009 seeds the three canonical
    names into every test database, so a plain create() of "1 of 1" collides
    on the unique name. Shaping the seeded row is also closer to what an
    operator does — these are reference data they edit, not rows they mint.
    """
    policy, _created = ConsensusPolicy.objects.update_or_create(
        name=name,
        defaults={
            "required_reviews": reviews,
            "required_accepts": accepts,
            "reject_threshold": rejects,
            "changes_threshold": changes,
        },
    )
    return policy


class AssignmentTests(TestCase):
    def test_claiming_twice_returns_the_same_live_claim(self):
        _u, owner, _p, _c, _m, task = _world()
        first = work.open_assignment(task, owner)
        again = work.open_assignment(task, owner)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(Assignment.objects.count(), 1)

    def test_releasing_then_reclaiming_makes_a_second_row(self):
        """History, not a toggle: both stints are on the record."""
        _u, owner, _p, _c, _m, task = _world()
        first = work.open_assignment(task, owner)
        work.release_assignment(task, owner)
        second = work.open_assignment(task, owner)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(Assignment.objects.count(), 2)
        first.refresh_from_db()
        self.assertIsNotNone(first.released_at)

    def test_the_database_refuses_two_live_claims(self):
        """The guard is partial-unique, so released rows do not collide."""
        _u, owner, _p, _c, _m, task = _world()
        Assignment.objects.create(task=task, person=owner)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Assignment.objects.create(task=task, person=owner)

    def test_one_mission_takes_many_contributors(self):
        """The whole reason Assignment exists beside Task.assignee."""
        _u, _owner, _p, _c, mission, _t = _world()
        for i in range(3):
            _user, person = _person(f"contrib{i}")
            task = Task.objects.create(mission=mission, title=f"T{i}")
            work.open_assignment(task, person)
        self.assertEqual(
            Assignment.objects.filter(task__mission=mission).count(), 3)


class SubmissionImmutabilityTests(TestCase):
    def test_a_draft_is_freely_editable(self):
        _u, owner, _p, _c, _m, task = _world()
        sub = work.start_submission(task, owner, notes="first")
        sub.notes = "second"
        sub.save()
        sub.refresh_from_db()
        self.assertEqual(sub.notes, "second")

    def test_submitting_is_a_one_way_door(self):
        _u, owner, _p, _c, _m, task = _world()
        sub = work.submit(work.start_submission(task, owner))
        self.assertEqual(sub.state, SubmissionState.SUBMITTED)
        self.assertIsNotNone(sub.submitted_at)
        with self.assertRaises(ValidationError):
            work.submit(sub)

    def test_a_submitted_submission_refuses_a_content_edit(self):
        _u, owner, _p, _c, _m, task = _world()
        work.submit(work.start_submission(task, owner, notes="as claimed"))
        fresh = Submission.objects.get()
        fresh.notes = "quietly rewritten"
        with self.assertRaises(ValidationError):
            fresh.save()
        self.assertEqual(Submission.objects.get().notes, "as claimed")

    def test_resolution_is_still_writable_after_the_freeze(self):
        """The freeze must not lock out consensus, which writes last."""
        policy = _policy()
        _u, owner, project, _c, _m, task = _world(policy)
        sub = work.submit(work.start_submission(task, owner))
        _ru, reviewer = _reviewer(project, "rev")
        work.record_review(sub, reviewer, ReviewVerdict.ACCEPT)
        resolved = work.resolve(sub)
        self.assertEqual(resolved.resolution, SubmissionResolution.ACCEPTED)

    def test_the_constraint_catches_what_save_never_sees(self):
        """`update()` bypasses save(), which is why the CheckConstraint exists.

        This is the machinery path — bulk loaders, data migrations — that a
        save() guard alone would leave open.
        """
        _u, owner, _p, _c, _m, task = _world()
        sub = work.start_submission(task, owner)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Submission.objects.filter(pk=sub.pk).update(
                state=SubmissionState.SUBMITTED, submitted_at=None)

    def test_a_resolution_cannot_exist_without_its_timestamp(self):
        _u, owner, _p, _c, _m, task = _world()
        sub = work.start_submission(task, owner)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Submission.objects.filter(pk=sub.pk).update(
                resolution=SubmissionResolution.ACCEPTED, resolved_at=None)

    def test_a_correction_is_a_new_row_that_supersedes(self):
        _u, owner, _p, _c, _m, task = _world()
        first = work.submit(work.start_submission(task, owner, notes="typo"))
        second = work.correct(first, owner, notes="fixed")
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(second.supersedes_id, first.pk)
        self.assertEqual(list(first.corrections.all()), [second])
        first.refresh_from_db()
        self.assertEqual(first.notes, "typo")

    def test_a_draft_cannot_be_superseded(self):
        _u, owner, _p, _c, _m, task = _world()
        draft = work.start_submission(task, owner)
        with self.assertRaises(ValidationError):
            work.start_submission(task, owner, supersedes=draft)


class ReviewTests(TestCase):
    def test_a_draft_cannot_be_reviewed(self):
        _u, owner, project, _c, _m, task = _world(_policy())
        draft = work.start_submission(task, owner)
        _ru, reviewer = _reviewer(project, "rev")
        with self.assertRaises(ValidationError):
            work.record_review(draft, reviewer, ReviewVerdict.ACCEPT)

    def test_one_review_per_reviewer_and_a_change_of_mind_updates_it(self):
        _u, owner, project, _c, _m, task = _world(_policy(reviews=3, accepts=3, rejects=3))
        sub = work.submit(work.start_submission(task, owner))
        _ru, reviewer = _reviewer(project, "rev")
        work.record_review(sub, reviewer, ReviewVerdict.REQUEST_CHANGES, "hmm")
        work.record_review(sub, reviewer, ReviewVerdict.ACCEPT, "convinced")
        self.assertEqual(Review.objects.count(), 1)
        self.assertEqual(Review.objects.get().verdict, ReviewVerdict.ACCEPT)

    def test_the_database_refuses_a_second_review_row(self):
        _u, owner, project, _c, _m, task = _world(_policy())
        sub = work.submit(work.start_submission(task, owner))
        _ru, reviewer = _reviewer(project, "rev")
        Review.objects.create(submission=sub, reviewer=reviewer,
                              verdict=ReviewVerdict.ACCEPT)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Review.objects.create(submission=sub, reviewer=reviewer,
                                  verdict=ReviewVerdict.REJECT)

    def test_a_resolved_submission_refuses_further_reviews(self):
        _u, owner, project, _c, _m, task = _world(_policy())
        sub = work.submit(work.start_submission(task, owner))
        _ru, reviewer = _reviewer(project, "rev")
        work.record_review(sub, reviewer, ReviewVerdict.ACCEPT)
        work.resolve(sub)
        sub.refresh_from_db()
        _lu, latecomer = _reviewer(project, "late")
        with self.assertRaises(ValidationError):
            work.record_review(sub, latecomer, ReviewVerdict.REJECT)


class EligibilityTests(TestCase):
    def test_nobody_reviews_their_own_submission(self):
        _u, owner, project, _c, _m, task = _world(_policy())
        reviewer_user, reviewer = _reviewer(project, "rev")
        sub = work.submit(work.start_submission(task, reviewer))
        self.assertFalse(work.can_review(reviewer_user, sub))

    def test_not_even_a_superuser_reviews_their_own(self):
        """Staff bypass the roster, never the self-review rule."""
        _u, owner, _p, _c, _m, task = _world(_policy())
        staff_user, staff_person = _person("admin", staff=True)
        sub = work.submit(work.start_submission(task, staff_person))
        self.assertFalse(work.can_review(staff_user, sub))

    def test_a_project_reviewer_may_review(self):
        _u, owner, project, _c, _m, task = _world(_policy())
        sub = work.submit(work.start_submission(task, owner))
        reviewer_user, _r = _reviewer(project, "rev")
        self.assertTrue(work.can_review(reviewer_user, sub))

    def test_an_outsider_may_not(self):
        _u, owner, _p, _c, _m, task = _world(_policy())
        sub = work.submit(work.start_submission(task, owner))
        outsider_user, _o = _person("outsider")
        self.assertFalse(work.can_review(outsider_user, sub))

    def test_a_named_roster_narrows_rather_than_widens(self):
        """Once anybody is named, project-wide reviewers stop qualifying."""
        _u, owner, project, _c, _m, task = _world(_policy())
        sub = work.submit(work.start_submission(task, owner))
        project_reviewer_user, _pr = _reviewer(project, "projrev")
        self.assertTrue(work.can_review(project_reviewer_user, sub))

        named_user, named = _person("named")
        TaskReviewer.objects.create(task=task, person=named)
        self.assertTrue(work.can_review(named_user, sub))
        self.assertFalse(work.can_review(project_reviewer_user, sub))

    def test_anonymous_may_not(self):
        from django.contrib.auth.models import AnonymousUser
        _u, owner, _p, _c, _m, task = _world(_policy())
        sub = work.submit(work.start_submission(task, owner))
        self.assertFalse(work.can_review(AnonymousUser(), sub))


class ConsensusTests(TestCase):
    def _submitted(self, policy):
        _u, owner, project, _c, _m, task = _world(policy)
        sub = work.submit(work.start_submission(task, owner))
        return project, sub

    #: A running counter, because a test may vote in more than one batch and
    #: two reviewers cannot share a username.
    _seq = 0

    def _vote(self, project, sub, verdicts):
        for verdict in verdicts:
            ConsensusTests._seq += 1
            _ru, person = _reviewer(project, f"rev{ConsensusTests._seq}")
            work.record_review(sub, person, verdict)

    def test_no_policy_means_no_gate(self):
        """The state every pre-existing board is in."""
        _u, owner, _p, _c, _m, task = _world(None)
        sub = work.submit(work.start_submission(task, owner))
        self.assertIsNone(work.policy_for(sub))
        self.assertEqual(work.evaluate(sub), SubmissionResolution.PENDING)

    def test_one_of_one_accepts(self):
        project, sub = self._submitted(_policy())
        self._vote(project, sub, [ReviewVerdict.ACCEPT])
        self.assertEqual(work.evaluate(sub), SubmissionResolution.ACCEPTED)

    def test_two_of_three_needs_two(self):
        policy = _policy("2 of 3", reviews=3, accepts=2, rejects=2)
        project, sub = self._submitted(policy)
        self._vote(project, sub, [ReviewVerdict.ACCEPT])
        self.assertEqual(work.evaluate(sub), SubmissionResolution.PENDING)
        self._vote(project, sub, [ReviewVerdict.ACCEPT])
        self.assertEqual(work.evaluate(sub), SubmissionResolution.ACCEPTED)

    def test_three_of_five_needs_three(self):
        policy = _policy("3 of 5", reviews=5, accepts=3, rejects=3)
        project, sub = self._submitted(policy)
        self._vote(project, sub, [ReviewVerdict.ACCEPT] * 2)
        self.assertEqual(work.evaluate(sub), SubmissionResolution.PENDING)
        self._vote(project, sub, [ReviewVerdict.ACCEPT])
        self.assertEqual(work.evaluate(sub), SubmissionResolution.ACCEPTED)

    def test_rejects_resolve_as_rejected(self):
        policy = _policy("2 of 3", reviews=3, accepts=2, rejects=2)
        project, sub = self._submitted(policy)
        self._vote(project, sub, [ReviewVerdict.REJECT] * 2)
        self.assertEqual(work.evaluate(sub), SubmissionResolution.REJECTED)

    def test_change_requests_resolve_as_changes_requested(self):
        policy = _policy("2 of 3", reviews=3, accepts=2, rejects=2)
        project, sub = self._submitted(policy)
        self._vote(project, sub, [ReviewVerdict.REQUEST_CHANGES] * 2)
        self.assertEqual(work.evaluate(sub),
                         SubmissionResolution.CHANGES_REQUESTED)

    def test_changes_threshold_falls_back_to_reject_threshold(self):
        policy = _policy("2 of 3", reviews=3, accepts=2, rejects=2, changes=None)
        self.assertEqual(policy.effective_changes_threshold, 2)

    def test_rejection_wins_a_tie(self):
        """Deliberate: accepting contested material is the expensive mistake."""
        policy = _policy("2 of 4", reviews=4, accepts=2, rejects=2)
        project, sub = self._submitted(policy)
        self._vote(project, sub, [ReviewVerdict.ACCEPT] * 2)
        self._vote(project, sub, [ReviewVerdict.REJECT] * 2)
        self.assertEqual(work.evaluate(sub), SubmissionResolution.REJECTED)

    def test_evaluate_writes_nothing(self):
        policy = _policy()
        project, sub = self._submitted(policy)
        self._vote(project, sub, [ReviewVerdict.ACCEPT])
        work.evaluate(sub)
        work.evaluate(sub)
        sub.refresh_from_db()
        self.assertEqual(sub.resolution, SubmissionResolution.PENDING)
        self.assertIsNone(sub.resolved_at)

    def test_a_policy_edit_changes_the_answer_without_touching_reviews(self):
        """The reason consensus counts rather than stamps."""
        policy = _policy("2 of 3", reviews=3, accepts=2, rejects=2)
        project, sub = self._submitted(policy)
        self._vote(project, sub, [ReviewVerdict.ACCEPT])
        self.assertEqual(work.evaluate(sub), SubmissionResolution.PENDING)
        policy.required_accepts = 1
        policy.save(update_fields=["required_accepts"])
        sub.refresh_from_db()
        self.assertEqual(work.evaluate(sub), SubmissionResolution.ACCEPTED)
        self.assertEqual(Review.objects.count(), 1)

    def test_a_policy_needing_more_accepts_than_reviews_is_refused(self):
        policy = ConsensusPolicy(
            name="impossible", required_reviews=2, required_accepts=3)
        with self.assertRaises(ValidationError):
            policy.full_clean()


class ResolveTests(TestCase):
    def _accepted(self):
        policy = _policy()
        _u, owner, project, _c, _m, task = _world(policy)
        sub = work.submit(work.start_submission(task, owner))
        _ru, reviewer = _reviewer(project, "rev")
        work.record_review(sub, reviewer, ReviewVerdict.ACCEPT)
        return sub

    def test_resolve_records_the_outcome_once(self):
        sub = self._accepted()
        resolved = work.resolve(sub)
        self.assertEqual(resolved.resolution, SubmissionResolution.ACCEPTED)
        self.assertIsNotNone(resolved.resolved_at)

    def test_resolve_is_idempotent(self):
        """Stage 2 hangs reward settlement off this transition; twice would pay twice."""
        sub = self._accepted()
        first = work.resolve(sub)
        stamp = first.resolved_at
        second = work.resolve(sub)
        self.assertEqual(second.resolved_at, stamp)
        self.assertEqual(second.resolution, SubmissionResolution.ACCEPTED)

    def test_resolve_is_a_no_op_while_pending(self):
        policy = _policy("2 of 3", reviews=3, accepts=2, rejects=2)
        _u, owner, project, _c, _m, task = _world(policy)
        sub = work.submit(work.start_submission(task, owner))
        _ru, reviewer = _reviewer(project, "rev")
        work.record_review(sub, reviewer, ReviewVerdict.ACCEPT)
        out = work.resolve(sub)
        self.assertEqual(out.resolution, SubmissionResolution.PENDING)
        self.assertIsNone(out.resolved_at)

    def test_resolve_does_nothing_without_a_policy(self):
        _u, owner, _p, _c, _m, task = _world(None)
        sub = work.submit(work.start_submission(task, owner))
        out = work.resolve(sub)
        self.assertEqual(out.resolution, SubmissionResolution.PENDING)
