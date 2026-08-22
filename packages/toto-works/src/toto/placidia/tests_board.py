"""The Bounty Board view.

The assertion this suite exists for is the mesh one: the board is
LoginRequired but NOT `in_data_mesh`-gated, unlike every other kanban surface.
That is a deliberate divergence and the kind of thing a later reader "fixes"
into consistency — so it is pinned here with the reason.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from toto.api.testutils import add_to_mesh
from toto.core.models import Platform
from toto.kanban import work
from toto.kanban.models import (
    Campaign, ConsensusPolicy, Mission, MissionVisibility, Practitioner,
    Project, ProjectCommitment, ReviewVerdict, RewardPolicy, RewardTrigger,
)
from toto.people.models import Person
from toto.placidia import services
from toto.placidia.models import PlacidiaBounty, PlacidiaCampaign

User = get_user_model()

BOARD = "/placidia/"


class BoardTestBase(TestCase):
    def setUp(self):
        Platform.objects.create(
            site_name="Test", author="t", publication_year=2026, active=True)
        self.user = User.objects.create_user(username="viewer", password="p")
        self.person = Person.objects.create(
            user=self.user, display_name="Viewer", email="v@x.com")
        self.project = Project.objects.create(name="P", project_lead=self.person)
        self.campaign = Campaign.objects.create(project=self.project, name="Rivers")
        self.pcampaign = PlacidiaCampaign.objects.create(campaign=self.campaign)
        self.rule = ConsensusPolicy.objects.get(name="1 of 1")

    def _bounty(self, title="Photograph the bridges", **kw):
        mission = Mission.objects.create(
            campaign=self.campaign, title=title, consensus_policy=self.rule,
            **{k: v for k, v in kw.items() if k in {"visibility"}})
        return PlacidiaBounty.objects.create(
            mission=mission,
            instructions="One photo per bridge.",
            **{k: v for k, v in kw.items() if k not in {"visibility"}})


class AccessTests(BoardTestBase):
    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(BOARD)
        self.assertEqual(response.status_code, 302)

    def test_a_signed_in_user_sees_the_board(self):
        self._bounty()
        self.client.force_login(self.user)
        response = self.client.get(BOARD)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Photograph the bridges")

    def test_a_user_outside_the_data_mesh_still_sees_it(self):
        """THE deliberate divergence from every other kanban surface.

        MissionDetailView renders access-denied for a non-mesh user. The
        Bounty Board must not: crowdsourcing depends on people outside the
        core team seeing what needs collecting, and a mesh gate would show the
        board to exactly the people who are not its audience.
        """
        self._bounty()
        outsider = User.objects.create_user(username="outsider", password="p")
        Person.objects.create(
            user=outsider, display_name="Out", email="o@x.com")
        self.client.force_login(outsider)

        response = self.client.get(BOARD)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Photograph the bridges")

    def test_a_mesh_member_sees_it_too(self):
        self._bounty()
        self.client.force_login(add_to_mesh(self.user))
        self.assertEqual(self.client.get(BOARD).status_code, 200)

    def test_a_private_mission_never_appears(self):
        """Visibility is still enforced — through kanban's own helper."""
        self._bounty(title="Secret survey", visibility=MissionVisibility.PRIVATE)
        outsider = User.objects.create_user(username="outsider2", password="p")
        Person.objects.create(
            user=outsider, display_name="Out", email="o2@x.com")
        self.client.force_login(outsider)
        response = self.client.get(BOARD)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Secret survey")


class FilterTests(BoardTestBase):
    def setUp(self):
        super().setUp()
        self.open_bounty = self._bounty(title="Riverbank survey")
        self.shut = self._bounty(
            title="Winter survey",
            closes_at=timezone.now() - timezone.timedelta(days=1))
        self.client.force_login(self.user)

    def test_open_is_the_default(self):
        response = self.client.get(BOARD)
        self.assertContains(response, "Riverbank survey")
        self.assertNotContains(response, "Winter survey")

    def test_closed_can_be_asked_for(self):
        response = self.client.get(BOARD, {"show": "closed"})
        self.assertContains(response, "Winter survey")
        self.assertNotContains(response, "Riverbank survey")

    def test_all_shows_both(self):
        response = self.client.get(BOARD, {"show": "all"})
        self.assertContains(response, "Riverbank survey")
        self.assertContains(response, "Winter survey")

    def test_a_nonsense_show_falls_back_to_open(self):
        response = self.client.get(BOARD, {"show": "sideways"})
        self.assertEqual(response.context["show"], "open")

    def test_search_matches_title(self):
        response = self.client.get(BOARD, {"q": "Riverbank", "show": "all"})
        self.assertContains(response, "Riverbank survey")
        self.assertNotContains(response, "Winter survey")

    def test_the_empty_state_explains_itself(self):
        self.open_bounty.delete()
        response = self.client.get(BOARD)
        self.assertContains(response, "Nothing to collect just now.")


class ProgressTests(BoardTestBase):
    def setUp(self):
        super().setUp()
        self.bounty = self._bounty()
        self.client.force_login(self.user)

    def _reviewer(self, username):
        user = User.objects.create_user(username=username, password="p")
        person = Person.objects.create(
            user=user, display_name=username, email=f"{username}@x.com")
        practitioner = Practitioner.objects.create(
            person=person, role=Practitioner.ROLE_REVIEWER)
        ProjectCommitment.objects.create(
            practitioner=practitioner, project=self.project, hours_per_day=8)
        return person

    def _contributor(self, username):
        user = User.objects.create_user(username=username, password="p")
        return Person.objects.create(
            user=user, display_name=username, email=f"{username}@x.com")

    def test_counts_reflect_submissions_reviews_and_acceptances(self):
        # one still in review
        pending = services.contribute(self.bounty, self._contributor("c1"))
        work.submit(pending)
        # one accepted and turned into an observation
        accepted = services.contribute(self.bounty, self._contributor("c2"))
        work.submit(accepted)
        work.record_review(accepted, self._reviewer("r1"), ReviewVerdict.ACCEPT)
        with self.captureOnCommitCallbacks(execute=True):
            work.resolve(accepted)
        accepted.refresh_from_db()
        services.accept(accepted)

        bounty = self.client.get(BOARD).context["bounties"][0]
        self.assertEqual(bounty.n_submissions, 2)
        self.assertEqual(bounty.n_awaiting, 1)
        self.assertEqual(bounty.n_accepted, 1)

    def test_a_draft_is_not_counted_as_sent(self):
        services.contribute(self.bounty, self._contributor("c3"))
        bounty = self.client.get(BOARD).context["bounties"][0]
        self.assertEqual(bounty.n_submissions, 0)

    def test_the_reward_chip_comes_from_the_reward_policy(self):
        """Not from reward_summary, which is prose somebody may not have updated."""
        RewardPolicy.objects.create(
            mission=self.bounty.mission,
            trigger=RewardTrigger.SUBMISSION_ACCEPTED,
            asset_code="GEM", amount_base_units=25,
            funding_account_code="purse")
        response = self.client.get(BOARD)
        self.assertContains(response, "25 GEM")

    def test_a_campaign_reward_reaches_its_bounties(self):
        RewardPolicy.objects.create(
            campaign=self.campaign,
            trigger=RewardTrigger.SUBMISSION_ACCEPTED,
            asset_code="GEM", amount_base_units=7,
            funding_account_code="purse")
        response = self.client.get(BOARD)
        self.assertContains(response, "7 GEM")

    def test_a_bounty_full_of_contributions_reads_as_closed(self):
        self.bounty.max_contributions = 1
        self.bounty.save(update_fields=["max_contributions"])
        submission = services.contribute(self.bounty, self._contributor("c4"))
        work.submit(submission)
        response = self.client.get(BOARD)
        self.assertNotContains(response, "Photograph the bridges")
