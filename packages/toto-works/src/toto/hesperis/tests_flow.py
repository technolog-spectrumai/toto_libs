"""The contributor's and reviewer's journeys, end to end through the views.

tests_domain covers the guarantees at the service layer; this covers the doors
people actually use. The two that matter most here:

* contributing must attach files BEFORE the one-way door closes, or the
  submission freezes empty;
* reviewing must be unable to reach your own contribution, through the view and
  not merely through the helper.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from toto.core.models import Platform
from toto.kanban import work
from toto.kanban.models import (
    Campaign, ConsensusPolicy, Mission, MissionVisibility, Practitioner,
    Project, ProjectCommitment, Review, ReviewVerdict, Submission,
    SubmissionResolution, SubmissionState,
)
from toto.people.models import Person
from toto.hesperis import services
from toto.hesperis.models import (
    AcceptedObservation, Dataset, DatasetVersion, HesperisBounty,
    HesperisCampaign,
)

User = get_user_model()


class FlowTestBase(TestCase):
    def setUp(self):
        Platform.objects.create(
            site_name="Test", author="t", publication_year=2026, active=True)
        self.lead_user, self.lead = self._person("lead")
        self.project = Project.objects.create(name="P", project_lead=self.lead)
        self.campaign = Campaign.objects.create(project=self.project, name="Rivers")
        self.pcampaign = HesperisCampaign.objects.create(campaign=self.campaign)
        self.rule = ConsensusPolicy.objects.get(name="1 of 1")
        self.mission = Mission.objects.create(
            campaign=self.campaign, title="Photograph the bridges",
            consensus_policy=self.rule)
        self.bounty = HesperisBounty.objects.create(
            mission=self.mission, instructions="One photo per bridge.")

    @staticmethod
    def _person(username, *, staff=False):
        user = User.objects.create_user(
            username=username, password="p", is_staff=staff)
        return user, Person.objects.create(
            user=user, display_name=username.title(), email=f"{username}@x.com")

    def _reviewer(self, username="rev"):
        user, person = self._person(username)
        practitioner = Practitioner.objects.create(
            person=person, role=Practitioner.ROLE_REVIEWER)
        ProjectCommitment.objects.create(
            practitioner=practitioner, project=self.project, hours_per_day=8)
        return user, person


class BountyDetailTests(FlowTestBase):
    def test_it_renders_with_instructions_and_rule(self):
        self.client.force_login(self.lead_user)
        response = self.client.get(f"/hesperis/bounty/{self.bounty.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "One photo per bridge.")
        self.assertContains(response, "1 of 1")

    def test_a_private_bounty_404s_for_an_outsider(self):
        self.mission.visibility = MissionVisibility.PRIVATE
        self.mission.save(update_fields=["visibility"])
        outsider, _p = self._person("outsider")
        self.client.force_login(outsider)
        response = self.client.get(f"/hesperis/bounty/{self.bounty.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_it_lists_only_my_own_contributions(self):
        mine_user, mine = self._person("mine")
        other_user, other = self._person("other")
        work.submit(services.contribute(self.bounty, mine, notes="my note"))
        work.submit(services.contribute(self.bounty, other, notes="their note"))

        self.client.force_login(mine_user)
        response = self.client.get(f"/hesperis/bounty/{self.bounty.pk}/")
        self.assertContains(response, "my note")
        self.assertNotContains(response, "their note")


class ContributeTests(FlowTestBase):
    def setUp(self):
        super().setUp()
        self.user, self.person = self._person("contributor")
        self.client.force_login(self.user)
        self.url = f"/hesperis/bounty/{self.bounty.pk}/contribute/"

    def test_the_form_renders(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Contribute an observation")

    def test_posting_creates_a_submitted_contribution(self):
        response = self.client.post(self.url, {"notes": "Two arches, brick."})
        self.assertEqual(response.status_code, 302)
        submission = Submission.objects.get()
        self.assertEqual(submission.state, SubmissionState.SUBMITTED)
        self.assertEqual(submission.notes, "Two arches, brick.")
        self.assertEqual(submission.submitted_by, self.person)

    def test_it_creates_this_persons_own_task_under_the_bounty(self):
        self.client.post(self.url, {"notes": "x"})
        submission = Submission.objects.get()
        self.assertEqual(submission.task.mission, self.mission)
        self.assertTrue(submission.task.assignments.filter(
            person=self.person, released_at__isnull=True).exists())

    def test_a_closed_bounty_refuses(self):
        self.bounty.closes_at = timezone.now() - timezone.timedelta(days=1)
        self.bounty.save(update_fields=["closes_at"])
        response = self.client.post(self.url, {"notes": "late"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Submission.objects.count(), 0)

    def test_a_closed_bounty_shows_no_form(self):
        self.bounty.closes_at = timezone.now() - timezone.timedelta(days=1)
        self.bounty.save(update_fields=["closes_at"])
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_the_file_picker_is_scoped_to_this_user(self):
        """A picker offering files you have no claim on is a broken control."""
        response = self.client.get(self.url)
        self.assertEqual(
            list(response.context["form"].fields["files"].queryset), [])

    def test_a_second_contribution_is_a_separate_submission(self):
        self.client.post(self.url, {"notes": "first"})
        self.client.post(self.url, {"notes": "second"})
        self.assertEqual(Submission.objects.count(), 2)


class ReviewQueueTests(FlowTestBase):
    def setUp(self):
        super().setUp()
        self.contributor_user, self.contributor = self._person("contributor")
        self.reviewer_user, self.reviewer = self._reviewer("reviewer")
        self.submission = work.submit(services.contribute(
            self.bounty, self.contributor, notes="A stone bridge."))

    def test_a_reviewer_sees_a_pending_contribution(self):
        self.client.force_login(self.reviewer_user)
        response = self.client.get("/hesperis/review/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A stone bridge.")

    def test_a_contributor_never_sees_their_own(self):
        self.client.force_login(self.contributor_user)
        response = self.client.get("/hesperis/review/")
        self.assertNotContains(response, "A stone bridge.")

    def test_an_outsider_sees_nothing(self):
        outsider, _p = self._person("outsider")
        self.client.force_login(outsider)
        response = self.client.get("/hesperis/review/")
        self.assertNotContains(response, "A stone bridge.")

    def test_recording_an_accept_resolves_and_creates_the_observation(self):
        self.client.force_login(self.reviewer_user)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"/hesperis/review/{self.submission.pk}/",
                {"verdict": ReviewVerdict.ACCEPT, "comment": "clear photo"})
        self.assertEqual(response.status_code, 302)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.resolution, SubmissionResolution.ACCEPTED)
        self.assertEqual(AcceptedObservation.objects.count(), 1)
        self.assertEqual(
            AcceptedObservation.objects.get().observed_by, self.contributor)

    def test_a_reject_resolves_without_an_observation(self):
        self.client.force_login(self.reviewer_user)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                f"/hesperis/review/{self.submission.pk}/",
                {"verdict": ReviewVerdict.REJECT})
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.resolution, SubmissionResolution.REJECTED)
        self.assertEqual(AcceptedObservation.objects.count(), 0)

    def test_reviewing_your_own_is_refused_at_the_endpoint(self):
        """Not merely hidden from the queue — refused when posted directly."""
        self.client.force_login(self.contributor_user)
        self.client.post(
            f"/hesperis/review/{self.submission.pk}/",
            {"verdict": ReviewVerdict.ACCEPT})
        self.assertEqual(Review.objects.count(), 0)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.resolution, SubmissionResolution.PENDING)

    def test_a_two_of_three_rule_waits_for_the_third(self):
        self.mission.consensus_policy = ConsensusPolicy.objects.get(name="2 of 3")
        self.mission.save(update_fields=["consensus_policy"])

        self.client.force_login(self.reviewer_user)
        self.client.post(f"/hesperis/review/{self.submission.pk}/",
                         {"verdict": ReviewVerdict.ACCEPT})
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.resolution, SubmissionResolution.PENDING)
        self.assertEqual(AcceptedObservation.objects.count(), 0)

        second_user, _p = self._reviewer("reviewer2")
        self.client.force_login(second_user)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(f"/hesperis/review/{self.submission.pk}/",
                             {"verdict": ReviewVerdict.ACCEPT})
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.resolution, SubmissionResolution.ACCEPTED)
        self.assertEqual(AcceptedObservation.objects.count(), 1)

    def test_a_verdictless_post_changes_nothing(self):
        self.client.force_login(self.reviewer_user)
        self.client.post(f"/hesperis/review/{self.submission.pk}/", {})
        self.assertEqual(Review.objects.count(), 0)


class DatasetViewTests(FlowTestBase):
    def setUp(self):
        super().setUp()
        self.dataset = Dataset.objects.create(
            campaign=self.pcampaign, name="Bridges", slug="bridges")
        self.staff_user, self.staff = self._person("boss", staff=True)

    def _accept_one(self, username):
        _u, person = self._person(username)
        submission = work.submit(services.contribute(self.bounty, person))
        _ru, reviewer = self._reviewer(f"rev-{username}")
        work.record_review(submission, reviewer, ReviewVerdict.ACCEPT)
        with self.captureOnCommitCallbacks(execute=True):
            work.resolve(submission)
        submission.refresh_from_db()
        return services.accept(submission)

    def test_the_list_and_detail_render(self):
        self.client.force_login(self.lead_user)
        self.assertContains(self.client.get("/hesperis/datasets/"), "Bridges")
        self.assertContains(
            self.client.get(f"/hesperis/datasets/{self.dataset.pk}/"), "Bridges")

    def test_detail_reports_what_the_next_version_would_hold(self):
        self._accept_one("c1")
        self.client.force_login(self.lead_user)
        response = self.client.get(f"/hesperis/datasets/{self.dataset.pk}/")
        self.assertEqual(response.context["live_count"], 1)

    def test_only_staff_may_publish(self):
        self._accept_one("c1")
        self.client.force_login(self.lead_user)
        self.client.post(f"/hesperis/datasets/{self.dataset.pk}/freeze/")
        self.assertEqual(DatasetVersion.objects.count(), 0)

    def test_staff_publish_a_verifiable_version(self):
        self._accept_one("c1")
        self.client.force_login(self.staff_user)
        response = self.client.post(
            f"/hesperis/datasets/{self.dataset.pk}/freeze/", {"notes": "first cut"})
        self.assertEqual(response.status_code, 302)
        version = DatasetVersion.objects.get()
        self.assertEqual(version.number, 1)
        self.assertEqual(version.members.count(), 1)
        self.assertTrue(version.verify())

    def test_the_version_page_reports_verification(self):
        self._accept_one("c1")
        version = services.freeze(self.dataset, by=self.staff)
        self.client.force_login(self.lead_user)
        response = self.client.get(f"/hesperis/version/{version.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["verified"])
        self.assertContains(response, "Unchanged since it was published.")

    def test_the_version_page_flags_a_tampered_release(self):
        self._accept_one("c1")
        self._accept_one("c2")
        version = services.freeze(self.dataset, by=self.staff)
        version.members.first().delete()
        self.client.force_login(self.lead_user)
        response = self.client.get(f"/hesperis/version/{version.pk}/")
        self.assertFalse(response.context["verified"])
        self.assertContains(response, "no longer matches its manifest")

    def test_a_published_version_does_not_grow(self):
        self._accept_one("c1")
        version = services.freeze(self.dataset, by=self.staff)
        self._accept_one("c2")
        self.client.force_login(self.lead_user)
        response = self.client.get(f"/hesperis/version/{version.pk}/")
        self.assertEqual(len(response.context["members"]), 1)
        self.assertEqual(
            self.client.get(f"/hesperis/datasets/{self.dataset.pk}/")
            .context["live_count"], 2)
