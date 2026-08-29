"""Running a programme: the staff surface.

Two properties matter more than the rest. Non-staff must be refused at every
door — a contributor who can create bounties is a contributor who can pay
themselves. And a bounty whose data was accepted must NOT be deletable,
because its observations are provenance for a dataset.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from toto.core.models import Platform
from toto.kanban import rewards, work
from toto.kanban.models import (
    Campaign, ConsensusPolicy, Mission, Practitioner, Project,
    ProjectCommitment, ReviewVerdict, RewardGrant, RewardGrantState,
    RewardPolicy, RewardTrigger, Submission,
)
from toto.people.models import Person
from toto.hesperis import services
from toto.hesperis.models import AcceptedObservation, HesperisBounty, HesperisCampaign

User = get_user_model()


class RecordingBackend(rewards.RewardBackend):
    def settle(self, grant):
        rewards._finish(grant, RewardGrantState.SETTLED, "paid")


def _gem():
    """A ledger asset to pay in, with real provenance.

    `make_asset` mints it the way the issuance path does — every Asset carries
    a signed genesis hash behind a CHECK constraint, so a bare
    `Asset.objects.create` is refused by the database."""
    from toto.assets.testing import make_asset
    return make_asset(unit_name="GEM", name="Gem", decimals=0, code="GEM")


class StaffTestBase(TestCase):
    def setUp(self):
        Platform.objects.create(
            site_name="Test", author="t", publication_year=2026, active=True)
        self.gem = _gem()
        self.staff_user, self.staff = self._person("boss", staff=True)
        self.plain_user, self.plain = self._person("plain")
        self.project = Project.objects.create(name="P", project_lead=self.staff)
        self.rule = ConsensusPolicy.objects.get(name="1 of 1")

    @staticmethod
    def _person(username, *, staff=False):
        user = User.objects.create_user(username=username, password="p", is_staff=staff)
        return user, Person.objects.create(
            user=user, display_name=username.title(), email=f"{username}@x.com")

    def _campaign(self, name="Rivers"):
        campaign = Campaign.objects.create(
            project=self.project, name=name, consensus_policy=self.rule)
        return campaign, HesperisCampaign.objects.create(campaign=campaign)

    def _bounty(self, campaign, title="Bridges"):
        mission = Mission.objects.create(campaign=campaign, title=title)
        return HesperisBounty.objects.create(mission=mission)


class AccessTests(StaffTestBase):
    URLS = ["/hesperis/manage/", "/hesperis/manage/campaign/new/",
            "/hesperis/manage/bounty/new/"]

    def test_non_staff_are_refused_everywhere(self):
        self.client.force_login(self.plain_user)
        for url in self.URLS:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertIn(response.status_code, (302, 403))
                self.assertNotEqual(response.status_code, 200)

    def test_non_staff_cannot_post_a_bounty(self):
        campaign, _pc = self._campaign()
        self.client.force_login(self.plain_user)
        self.client.post("/hesperis/manage/bounty/new/", {
            "campaign": campaign.pk, "title": "Sneaky", "reward_amount": 999,
            "reward_asset": self.gem.pk})
        self.assertEqual(HesperisBounty.objects.count(), 0)

    def test_non_staff_cannot_distribute(self):
        self.client.force_login(self.plain_user)
        response = self.client.post("/hesperis/manage/rewards/distribute/")
        self.assertNotEqual(response.status_code, 200)

    def test_staff_see_the_hub(self):
        self.client.force_login(self.staff_user)
        response = self.client.get("/hesperis/manage/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manage Hesperis")

    def test_the_board_shows_staff_the_manage_link(self):
        self.client.force_login(self.staff_user)
        self.assertContains(self.client.get("/hesperis/"), "/hesperis/manage/")
        self.client.force_login(self.plain_user)
        self.assertNotContains(self.client.get("/hesperis/"), "/hesperis/manage/")


class CampaignCreateTests(StaffTestBase):
    def test_it_creates_the_kanban_campaign_and_its_extension(self):
        self.client.force_login(self.staff_user)
        response = self.client.post("/hesperis/manage/campaign/new/", {
            "project": self.project.pk, "name": "Vistula", "licence": "CC0",
            "consensus_policy": self.rule.pk, "is_open": "on"})
        self.assertEqual(response.status_code, 302)
        campaign = Campaign.objects.get(name="Vistula")
        self.assertEqual(campaign.consensus_policy, self.rule)
        self.assertEqual(campaign.owner, self.staff)
        self.assertEqual(campaign.hesperis.licence, "CC0")
        self.assertTrue(campaign.hesperis.is_open)


class BountyCreateTests(StaffTestBase):
    def setUp(self):
        super().setUp()
        self.campaign, self.pc = self._campaign()
        self.client.force_login(self.staff_user)

    def test_it_creates_mission_extension_and_reward_together(self):
        response = self.client.post("/hesperis/manage/bounty/new/", {
            "campaign": self.campaign.pk, "title": "Photograph the bridges",
            "instructions": "One per span.", "consensus_policy": self.rule.pk,
            "reward_amount": 5, "reward_asset": self.gem.pk,
            "funding_account": "purse"})
        self.assertEqual(response.status_code, 302)
        bounty = HesperisBounty.objects.get()
        self.assertEqual(bounty.mission.title, "Photograph the bridges")
        self.assertEqual(bounty.mission.owner, self.staff)
        self.assertEqual(bounty.instructions, "One per span.")
        policy = RewardPolicy.objects.get()
        self.assertEqual(policy.mission, bounty.mission)
        self.assertEqual(policy.amount_base_units, 5)
        self.assertEqual(policy.funding_account_code, "purse")

    def test_no_reward_means_no_policy(self):
        self.client.post("/hesperis/manage/bounty/new/", {
            "campaign": self.campaign.pk, "title": "Unpaid"})
        self.assertEqual(HesperisBounty.objects.count(), 1)
        self.assertEqual(RewardPolicy.objects.count(), 0)

    def test_a_blank_funding_account_falls_back_to_the_treasury(self):
        from toto.assets.models import AccountType, LedgerAccount
        purse = LedgerAccount.objects.create(
            code="rivers_purse", name="Rivers", account_type=AccountType.SYSTEM)
        self.pc.treasury_account = purse
        self.pc.save(update_fields=["treasury_account"])
        self.client.post("/hesperis/manage/bounty/new/", {
            "campaign": self.campaign.pk, "title": "Paid",
            "reward_amount": 3, "reward_asset": self.gem.pk})
        self.assertEqual(RewardPolicy.objects.get().funding_account_code, "rivers_purse")

    def test_only_hesperis_campaigns_are_offered(self):
        plain = Campaign.objects.create(project=self.project, name="Just kanban")
        response = self.client.get("/hesperis/manage/bounty/new/")
        offered = list(response.context["form"].fields["campaign"].queryset)
        self.assertIn(self.campaign, offered)
        self.assertNotIn(plain, offered)

    def test_closes_before_opens_is_refused(self):
        now = timezone.now()
        response = self.client.post("/hesperis/manage/bounty/new/", {
            "campaign": self.campaign.pk, "title": "Backwards",
            "opens_at": (now + timezone.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
            "closes_at": now.strftime("%Y-%m-%dT%H:%M")})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(HesperisBounty.objects.count(), 0)

    def test_the_asset_is_a_dropdown_of_real_ledger_assets(self):
        """Free text used to be accepted here; 'GEM' typed on a host with no
        GEM asset produced a policy the backend could never settle."""
        response = self.client.get("/hesperis/manage/bounty/new/")
        offered = list(response.context["form"].fields["reward_asset"].queryset)
        self.assertEqual(offered, [self.gem])

    def test_an_inactive_asset_is_not_offered(self):
        from toto.assets.testing import make_asset
        make_asset(unit_name="OLD", name="Old", decimals=0, active=False)
        response = self.client.get("/hesperis/manage/bounty/new/")
        codes = [a.unit_name for a in
                 response.context["form"].fields["reward_asset"].queryset]
        self.assertNotIn("OLD", codes)

    def test_the_policy_stores_the_unit_name_the_backend_resolves(self):
        self.client.post("/hesperis/manage/bounty/new/", {
            "campaign": self.campaign.pk, "title": "Paid",
            "reward_amount": 4, "reward_asset": self.gem.pk})
        self.assertEqual(RewardPolicy.objects.get().asset_code, "GEM")

    def test_an_amount_without_an_asset_is_refused(self):
        response = self.client.post("/hesperis/manage/bounty/new/", {
            "campaign": self.campaign.pk, "title": "Half",
            "reward_amount": 4})
        self.assertEqual(response.status_code, 200)
        self.assertIn("reward_asset", response.context["form"].errors)
        self.assertEqual(HesperisBounty.objects.count(), 0)

    def test_with_no_assets_the_form_says_so(self):
        self.gem.delete()
        response = self.client.get("/hesperis/manage/bounty/new/")
        self.assertContains(response, "No ledger assets exist")

    def test_the_new_bounty_appears_on_the_board(self):
        self.client.post("/hesperis/manage/bounty/new/", {
            "campaign": self.campaign.pk, "title": "Photograph the bridges"})
        self.assertContains(self.client.get("/hesperis/"), "Photograph the bridges")


class BountyEditTests(StaffTestBase):
    def setUp(self):
        super().setUp()
        self.campaign, _pc = self._campaign()
        self.bounty = self._bounty(self.campaign)
        self.client.force_login(self.staff_user)
        self.url = f"/hesperis/manage/bounty/{self.bounty.pk}/edit/"

    def test_the_form_is_seeded_from_the_bounty(self):
        RewardPolicy.objects.create(
            mission=self.bounty.mission, trigger=RewardTrigger.SUBMISSION_ACCEPTED,
            asset_code="GEM", amount_base_units=7, funding_account_code="x")
        response = self.client.get(self.url)
        self.assertEqual(response.context["form"].initial["title"], "Bridges")
        self.assertEqual(response.context["form"].initial["reward_amount"], 7)

    def test_editing_updates_in_place(self):
        self.client.post(self.url, {
            "title": "Bridges, revised", "instructions": "Now with arches.",
            "reward_amount": 9, "reward_asset": self.gem.pk})
        self.bounty.refresh_from_db()
        self.assertEqual(self.bounty.mission.title, "Bridges, revised")
        self.assertEqual(self.bounty.instructions, "Now with arches.")
        self.assertEqual(RewardPolicy.objects.get().amount_base_units, 9)
        self.assertEqual(HesperisBounty.objects.count(), 1)

    def test_zeroing_the_reward_removes_the_policy(self):
        RewardPolicy.objects.create(
            mission=self.bounty.mission, trigger=RewardTrigger.SUBMISSION_ACCEPTED,
            asset_code="GEM", amount_base_units=7)
        self.client.post(self.url, {"title": "Bridges", "reward_amount": 0})
        self.assertEqual(RewardPolicy.objects.count(), 0)

    def test_toggle_closes_and_reopens(self):
        self.client.post(f"/hesperis/manage/bounty/{self.bounty.pk}/toggle/")
        self.bounty.refresh_from_db()
        self.assertFalse(self.bounty.is_open())
        self.client.post(f"/hesperis/manage/bounty/{self.bounty.pk}/toggle/")
        self.bounty.refresh_from_db()
        self.assertTrue(self.bounty.is_open())


class BountyDeleteTests(StaffTestBase):
    def setUp(self):
        super().setUp()
        self.campaign, _pc = self._campaign()
        self.bounty = self._bounty(self.campaign)
        self.client.force_login(self.staff_user)
        self.url = f"/hesperis/manage/bounty/{self.bounty.pk}/delete/"

    def _reviewer(self):
        _u, person = self._person("rev")
        practitioner = Practitioner.objects.create(
            person=person, role=Practitioner.ROLE_REVIEWER)
        ProjectCommitment.objects.create(
            practitioner=practitioner, project=self.project, hours_per_day=8)
        return person

    def test_an_empty_bounty_deletes_with_its_mission(self):
        mission_pk = self.bounty.mission_id
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(HesperisBounty.objects.count(), 0)
        self.assertFalse(Mission.objects.filter(pk=mission_pk).exists())

    def test_unaccepted_contributions_go_with_it(self):
        submission = work.submit(services.contribute(self.bounty, self.plain))
        self.client.post(self.url)
        self.assertFalse(Submission.objects.filter(pk=submission.pk).exists())

    def test_a_bounty_with_accepted_data_cannot_be_deleted(self):
        """Its observations are provenance for a dataset; PROTECT backs this."""
        submission = work.submit(services.contribute(self.bounty, self.plain))
        work.record_review(submission, self._reviewer(), ReviewVerdict.ACCEPT)
        work.resolve(submission)
        submission.refresh_from_db()
        services.accept(submission)

        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(HesperisBounty.objects.count(), 1)
        self.assertEqual(AcceptedObservation.objects.count(), 1)

        page = self.client.get(self.url)
        self.assertContains(page, "cannot be deleted")
        self.assertContains(page, "Close the bounty")


@override_settings(KANBAN_REWARD_BACKEND="toto.hesperis.tests_staff.RecordingBackend")
class DistributeTests(StaffTestBase):
    def setUp(self):
        super().setUp()
        self.campaign, _pc = self._campaign()
        self.bounty = self._bounty(self.campaign)
        self.policy = RewardPolicy.objects.create(
            mission=self.bounty.mission, trigger=RewardTrigger.SUBMISSION_ACCEPTED,
            asset_code="GEM", amount_base_units=5)
        self.client.force_login(self.staff_user)

    def _failed_grant(self):
        submission = work.submit(services.contribute(self.bounty, self.plain))
        grant = rewards.record_grant(self.policy, self.plain, submission=submission)
        grant.state = RewardGrantState.FAILED
        grant.detail = "No asset with symbol 'GEM'."
        grant.save()
        return grant

    def test_it_retries_failed_grants(self):
        grant = self._failed_grant()
        response = self.client.post("/hesperis/manage/rewards/distribute/")
        self.assertEqual(response.status_code, 302)
        grant.refresh_from_db()
        self.assertEqual(grant.state, RewardGrantState.SETTLED)

    def test_pressing_it_twice_pays_once(self):
        self._failed_grant()
        self.client.post("/hesperis/manage/rewards/distribute/")
        self.client.post("/hesperis/manage/rewards/distribute/")
        self.assertEqual(RewardGrant.objects.count(), 1)
        self.assertEqual(RewardGrant.objects.get().state, RewardGrantState.SETTLED)

    def test_the_hub_reports_what_is_owed(self):
        self._failed_grant()
        response = self.client.get("/hesperis/manage/")
        self.assertEqual(response.context["grants"]["failed"], 1)
        self.assertEqual(response.context["grants"]["owed"], 5)
        self.assertContains(response, "Distribute rewards")
