from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from toto.assets.models import Asset, LedgerAccount
from toto.assets.services.assets import create_asset
from toto.people.models import Person
from toto.socialhub.models import Community

from .models import (
    AssemblyDecision,
    AssemblyProposal,
    AssemblyProposalType,
    AssemblyStatus,
    AssemblyVoteChoice,
    CommunityAssemblyConfig,
    CommunityRule,
    CommunityTransactionFee,
    PollTax,
    PollTaxPayment,
    PollTaxPeriod,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_user(username):
    return User.objects.create_user(username=username, password="testpass")


def make_person(username, *, is_federal_agent=False):
    user = make_user(username)
    return Person.objects.create(display_name=username, user=user, is_federal_agent=is_federal_agent)


def make_community(name, *, is_federal_tribe=False):
    return Community.objects.create(name=name, is_federal_tribe=is_federal_tribe)


def make_reserve():
    return LedgerAccount.objects.create(code="reserve", name="Reserve", account_type="reserve")


def make_asset(unit_name, *, reserve=None, supply=Decimal("1000000"), decimals=2):
    if reserve is None:
        reserve = LedgerAccount.objects.create(
            code=f"reserve-{unit_name.lower()}",
            name=f"Reserve {unit_name}",
            account_type="reserve",
        )
    return create_asset(
        name=unit_name,
        unit_name=unit_name,
        total_supply=supply,
        decimals=decimals,
        reserve_account=reserve,
        reference=f"create-{unit_name.lower()}",
    )


def make_proposal(community, person, *, proposal_type=AssemblyProposalType.RULE, title="Test proposal", metadata=None):
    return AssemblyProposal.objects.create(
        community=community,
        proposal_type=proposal_type,
        title=title,
        body="Test body",
        status=AssemblyStatus.OPEN,
        opened_by=person,
        metadata=metadata or {},
    )


def vote(proposal, person, choice):
    from .models import AssemblyVote
    return AssemblyVote.objects.create(proposal=proposal, voter=person, vote=choice)


# ---------------------------------------------------------------------------
# Fee plugin
# ---------------------------------------------------------------------------

class FeePluginTests(TestCase):
    def setUp(self):
        self.community = make_community("Fees Town")
        self.asset = make_asset("TKN")

    def _plugin(self):
        from .fee_plugin import AssemblyFeePlugin
        return AssemblyFeePlugin()

    def test_no_fee_configured_returns_zero(self):
        self.assertEqual(self._plugin().get_fee_bps(self.asset, community=self.community), 0)

    def test_no_community_kwarg_returns_zero(self):
        CommunityTransactionFee.objects.create(
            community=self.community, asset=None, fee_bps=100,
            set_by=CommunityTransactionFee.SET_BY_ADMIN, active=True,
        )
        self.assertEqual(self._plugin().get_fee_bps(self.asset), 0)

    def test_blanket_fee_only(self):
        CommunityTransactionFee.objects.create(
            community=self.community, asset=None, fee_bps=200,
            set_by=CommunityTransactionFee.SET_BY_ADMIN, active=True,
        )
        self.assertEqual(self._plugin().get_fee_bps(self.asset, community=self.community), 200)

    def test_per_asset_fee_only(self):
        CommunityTransactionFee.objects.create(
            community=self.community, asset=self.asset, fee_bps=50,
            set_by=CommunityTransactionFee.SET_BY_ADMIN, active=True,
        )
        self.assertEqual(self._plugin().get_fee_bps(self.asset, community=self.community), 50)

    def test_blanket_plus_per_asset(self):
        CommunityTransactionFee.objects.create(
            community=self.community, asset=None, fee_bps=300,
            set_by=CommunityTransactionFee.SET_BY_ADMIN, active=True,
        )
        CommunityTransactionFee.objects.create(
            community=self.community, asset=self.asset, fee_bps=50,
            set_by=CommunityTransactionFee.SET_BY_ADMIN, active=True,
        )
        self.assertEqual(self._plugin().get_fee_bps(self.asset, community=self.community), 350)

    def test_negative_asset_adjustment_reduces_effective_fee(self):
        CommunityTransactionFee.objects.create(
            community=self.community, asset=None, fee_bps=300,
            set_by=CommunityTransactionFee.SET_BY_ADMIN, active=True,
        )
        CommunityTransactionFee.objects.create(
            community=self.community, asset=self.asset, fee_bps=-100,
            set_by=CommunityTransactionFee.SET_BY_ADMIN, active=True,
        )
        self.assertEqual(self._plugin().get_fee_bps(self.asset, community=self.community), 200)

    def test_negative_adjustment_floored_at_zero(self):
        CommunityTransactionFee.objects.create(
            community=self.community, asset=None, fee_bps=50,
            set_by=CommunityTransactionFee.SET_BY_ADMIN, active=True,
        )
        CommunityTransactionFee.objects.create(
            community=self.community, asset=self.asset, fee_bps=-200,
            set_by=CommunityTransactionFee.SET_BY_ADMIN, active=True,
        )
        self.assertEqual(self._plugin().get_fee_bps(self.asset, community=self.community), 0)

    def test_inactive_fee_excluded(self):
        CommunityTransactionFee.objects.create(
            community=self.community, asset=None, fee_bps=500,
            set_by=CommunityTransactionFee.SET_BY_ADMIN, active=False,
        )
        self.assertEqual(self._plugin().get_fee_bps(self.asset, community=self.community), 0)

    def test_registry_aggregates_plugin(self):
        from toto.assets.fee_plugins import FeePluginRegistry
        CommunityTransactionFee.objects.create(
            community=self.community, asset=None, fee_bps=150,
            set_by=CommunityTransactionFee.SET_BY_ADMIN, active=True,
        )
        total = FeePluginRegistry.get_total_fee_bps(self.asset, community=self.community)
        self.assertGreaterEqual(total, 150)


# ---------------------------------------------------------------------------
# Poll tax amount computation
# ---------------------------------------------------------------------------

class PollTaxAmountTests(TestCase):
    def setUp(self):
        self.community = make_community("Tax Town")
        self.asset = make_asset("PAY")
        self.person = make_person("taxpayer")
        self.person.communities.add(self.community)

    def _make_poll_tax(self, head=100, wealth_rate=0, wealth_asset=None):
        return PollTax.objects.create(
            community=self.community,
            payment_asset=self.asset,
            head_amount_base_units=head,
            wealth_rate_bps=wealth_rate,
            wealth_asset=wealth_asset,
            period=PollTaxPeriod.YEARLY,
            active=True,
        )

    def test_head_tax_only(self):
        pt = self._make_poll_tax(head=500)
        self.assertEqual(pt.compute_amount_for(self.person), 500)

    def test_zero_head_tax(self):
        pt = self._make_poll_tax(head=0)
        self.assertEqual(pt.compute_amount_for(self.person), 0)

    def test_federal_agent_exempt(self):
        agent = make_person("agent", is_federal_agent=True)
        agent.communities.add(self.community)
        pt = self._make_poll_tax(head=9999)
        self.assertEqual(pt.compute_amount_for(agent), 0)

    def test_federal_tribe_member_exempt(self):
        tribe = make_community("Federal Tribe", is_federal_tribe=True)
        member = make_person("tribemember")
        member.communities.add(self.community)
        member.communities.add(tribe)
        pt = self._make_poll_tax(head=9999)
        self.assertEqual(pt.compute_amount_for(member), 0)

    def test_regular_member_is_not_exempt(self):
        pt = self._make_poll_tax(head=100)
        self.assertEqual(pt.compute_amount_for(self.person), 100)

    def test_period_label_yearly(self):
        from django.utils import timezone
        pt = self._make_poll_tax()
        self.assertEqual(pt.current_period_label(), str(timezone.now().year))

    def test_period_label_monthly(self):
        from django.utils import timezone
        pt = PollTax.objects.create(
            community=self.community, payment_asset=self.asset,
            period=PollTaxPeriod.MONTHLY, active=True,
        )
        expected = timezone.now().strftime("%Y-%m")
        self.assertEqual(pt.current_period_label(), expected)


# ---------------------------------------------------------------------------
# Voting rights
# ---------------------------------------------------------------------------

class VotingRightsTests(TestCase):
    def setUp(self):
        self.community = make_community("Vote Town")
        self.asset = make_asset("VOT")
        self.person = make_person("voter")
        self.person.communities.add(self.community)

    def _has_rights(self, person=None):
        from .views import _has_voting_rights
        return _has_voting_rights(person or self.person, self.community)

    def test_no_poll_tax_means_full_rights(self):
        self.assertTrue(self._has_rights())

    def test_unpaid_poll_tax_blocks_voting(self):
        PollTax.objects.create(
            community=self.community, payment_asset=self.asset,
            head_amount_base_units=10, period=PollTaxPeriod.YEARLY, active=True,
        )
        self.assertFalse(self._has_rights())

    def test_paid_poll_tax_grants_voting(self):
        pt = PollTax.objects.create(
            community=self.community, payment_asset=self.asset,
            head_amount_base_units=10, period=PollTaxPeriod.YEARLY, active=True,
        )
        PollTaxPayment.objects.create(
            poll_tax=pt, person=self.person,
            period_label=pt.current_period_label(),
            total_paid_base_units=10,
        )
        self.assertTrue(self._has_rights())

    def test_federal_agent_has_rights_without_paying(self):
        PollTax.objects.create(
            community=self.community, payment_asset=self.asset,
            head_amount_base_units=10, period=PollTaxPeriod.YEARLY, active=True,
        )
        agent = make_person("fed-agent", is_federal_agent=True)
        agent.communities.add(self.community)
        self.assertTrue(self._has_rights(agent))

    def test_federal_tribe_member_has_rights_without_paying(self):
        PollTax.objects.create(
            community=self.community, payment_asset=self.asset,
            head_amount_base_units=10, period=PollTaxPeriod.YEARLY, active=True,
        )
        tribe = make_community("Tribe", is_federal_tribe=True)
        exempt = make_person("tribe-member")
        exempt.communities.add(self.community)
        exempt.communities.add(tribe)
        self.assertTrue(self._has_rights(exempt))

    def test_inactive_poll_tax_does_not_block_voting(self):
        PollTax.objects.create(
            community=self.community, payment_asset=self.asset,
            head_amount_base_units=10, period=PollTaxPeriod.YEARLY, active=False,
        )
        self.assertTrue(self._has_rights())


# ---------------------------------------------------------------------------
# Proposal enactment
# ---------------------------------------------------------------------------

class ProposalEnactmentTests(TestCase):
    def setUp(self):
        self.community = make_community("Governance Town")
        self.proposer = make_person("proposer")
        self.proposer.communities.add(self.community)
        CommunityAssemblyConfig.objects.create(community=self.community, quorum_fraction="0.5100")

    def _enact(self, proposal):
        from .views import _enact_proposal
        return _enact_proposal(proposal)

    def test_rule_proposal_creates_community_rule(self):
        p = make_proposal(self.community, self.proposer, title="No shoes indoors")
        decision = self._enact(p)
        p.refresh_from_db()
        self.assertEqual(p.status, AssemblyStatus.PASSED)
        self.assertTrue(CommunityRule.objects.filter(community=self.community, title="No shoes indoors", active=True).exists())
        self.assertIsNotNone(decision.pk)

    def test_rule_repeal_deactivates_existing_rule(self):
        decision_stub = AssemblyDecision.record(
            community=self.community, decision_type="rule",
            title="Shoes rule", content={"note": "original"},
        )
        CommunityRule.objects.create(
            community=self.community, decision=decision_stub,
            title="Shoes rule", text="Original text", active=True,
        )
        p = AssemblyProposal.objects.create(
            community=self.community,
            proposal_type=AssemblyProposalType.RULE,
            is_repeal=True,
            title="Shoes rule",
            body="",
            status=AssemblyStatus.OPEN,
            opened_by=self.proposer,
        )
        self._enact(p)
        self.assertFalse(CommunityRule.objects.filter(community=self.community, title="Shoes rule", active=True).exists())

    def test_asset_tax_proposal_creates_fee(self):
        asset = make_asset("TAX")
        p = make_proposal(
            self.community, self.proposer,
            proposal_type=AssemblyProposalType.ASSET_TAX,
            title="TAX 200bps",
            metadata={"fee_bps": 200, "asset_id": asset.pk},
        )
        self._enact(p)
        self.assertTrue(
            CommunityTransactionFee.objects.filter(
                community=self.community, asset=asset, fee_bps=200, active=True
            ).exists()
        )

    def test_asset_tax_enactment_deactivates_prior_fee(self):
        asset = make_asset("TAX2")
        CommunityTransactionFee.objects.create(
            community=self.community, asset=asset, fee_bps=100,
            set_by=CommunityTransactionFee.SET_BY_ADMIN, active=True,
        )
        p = make_proposal(
            self.community, self.proposer,
            proposal_type=AssemblyProposalType.ASSET_TAX,
            title="New TAX2 fee",
            metadata={"fee_bps": 500, "asset_id": asset.pk},
        )
        self._enact(p)
        self.assertFalse(
            CommunityTransactionFee.objects.filter(
                community=self.community, asset=asset, fee_bps=100, active=True
            ).exists()
        )
        self.assertTrue(
            CommunityTransactionFee.objects.filter(
                community=self.community, asset=asset, fee_bps=500, active=True
            ).exists()
        )

    def test_decision_immutable_after_creation(self):
        p = make_proposal(self.community, self.proposer, title="Immutable check")
        decision = self._enact(p)
        with self.assertRaises(ValueError):
            decision.save()

    def test_decision_hash_chain_valid_across_multiple(self):
        p1 = make_proposal(self.community, self.proposer, title="Rule A")
        p2 = make_proposal(self.community, self.proposer, title="Rule B")
        d1 = self._enact(p1)
        d2 = self._enact(p2)
        self.assertEqual(d2.prev_hash, d1.content_hash)
        self.assertNotEqual(d2.content_hash, d1.content_hash)


# ---------------------------------------------------------------------------
# Vote→enact/reject flow (via view helper)
# ---------------------------------------------------------------------------

class VoteFlowTests(TestCase):
    def setUp(self):
        self.community = make_community("Quorum City")
        CommunityAssemblyConfig.objects.create(community=self.community, quorum_fraction="0.5100")
        self.alice = make_person("alice-vote")
        self.bob = make_person("bob-vote")
        self.carol = make_person("carol-vote")
        for p in (self.alice, self.bob, self.carol):
            p.communities.add(self.community)

    def _cast(self, proposal, person, choice):
        from .views import _enact_proposal, _get_quorum_fraction
        from .models import AssemblyVote, AssemblyVoteChoice
        AssemblyVote.objects.create(proposal=proposal, voter=person, vote=choice)
        tally = proposal.tally()
        yes = tally[AssemblyVoteChoice.YES]
        no = tally[AssemblyVoteChoice.NO]
        total = yes + no
        qf = _get_quorum_fraction(self.community)
        if total > 0 and yes / total >= qf:
            _enact_proposal(proposal)
        elif total > 0 and no / total > (1 - qf):
            proposal.status = AssemblyStatus.REJECTED
            proposal.save(update_fields=["status"])

    def test_proposal_passes_when_quorum_reached(self):
        p = make_proposal(self.community, self.alice, title="Pass me")
        self._cast(p, self.alice, AssemblyVoteChoice.YES)
        self._cast(p, self.bob, AssemblyVoteChoice.YES)
        p.refresh_from_db()
        self.assertEqual(p.status, AssemblyStatus.PASSED)

    def test_proposal_rejected_when_no_supermajority(self):
        p = make_proposal(self.community, self.alice, title="Fail me")
        self._cast(p, self.alice, AssemblyVoteChoice.NO)
        self._cast(p, self.bob, AssemblyVoteChoice.NO)
        self._cast(p, self.carol, AssemblyVoteChoice.NO)
        p.refresh_from_db()
        self.assertEqual(p.status, AssemblyStatus.REJECTED)

    def test_abstain_does_not_count_toward_quorum(self):
        p = make_proposal(self.community, self.alice, title="Abstain test")
        self._cast(p, self.alice, AssemblyVoteChoice.YES)
        self._cast(p, self.bob, AssemblyVoteChoice.ABSTAIN)
        p.refresh_from_db()
        # 1 yes / 1 (yes+no) = 100% — should pass
        self.assertEqual(p.status, AssemblyStatus.PASSED)

    def test_no_votes_yet_proposal_stays_open(self):
        p = make_proposal(self.community, self.alice, title="Pending")
        p.refresh_from_db()
        self.assertEqual(p.status, AssemblyStatus.OPEN)


# ---------------------------------------------------------------------------
# View-level HTTP tests
# ---------------------------------------------------------------------------

class AssemblyViewTests(TestCase):
    def setUp(self):
        self.community = make_community("View Community")
        CommunityAssemblyConfig.objects.create(community=self.community, quorum_fraction="0.5100")

        self.user = make_user("viewuser")
        self.person = Person.objects.create(display_name="View User", user=self.user)
        self.person.communities.add(self.community)

        self.outsider_user = make_user("outsider")
        self.outsider = Person.objects.create(display_name="Outsider", user=self.outsider_user)

    def _login(self, user=None):
        self.client.force_login(user or self.user)

    def test_overview_requires_login(self):
        resp = self.client.get(reverse("assembly:overview"))
        self.assertRedirects(resp, f"/accounts/login/?next={reverse('assembly:overview')}", fetch_redirect_response=False)

    def test_overview_accessible_when_logged_in(self):
        self._login()
        resp = self.client.get(reverse("assembly:overview"))
        self.assertEqual(resp.status_code, 200)

    def test_community_assembly_requires_login(self):
        url = reverse("assembly:community_assembly", kwargs={"slug": self.community.slug})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)

    def test_community_assembly_requires_membership(self):
        self._login(self.outsider_user)
        url = reverse("assembly:community_assembly", kwargs={"slug": self.community.slug})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

    def test_community_assembly_accessible_for_member(self):
        self._login()
        url = reverse("assembly:community_assembly", kwargs={"slug": self.community.slug})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_proposal_vote_blocked_by_unpaid_poll_tax(self):
        asset = make_asset("BLK")
        PollTax.objects.create(
            community=self.community, payment_asset=asset,
            head_amount_base_units=50, period=PollTaxPeriod.YEARLY, active=True,
        )
        proposal = make_proposal(self.community, self.person, title="Tax blocked")
        self._login()
        url = reverse("assembly:proposal_vote", kwargs={"slug": self.community.slug, "proposal_id": proposal.pk})
        resp = self.client.post(url, {"vote": "yes"})
        self.assertRedirects(resp, reverse("assembly:community_assembly", kwargs={"slug": self.community.slug}), fetch_redirect_response=False)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, AssemblyStatus.OPEN)

    def test_proposal_vote_recorded_when_poll_tax_paid(self):
        asset = make_asset("PD")
        pt = PollTax.objects.create(
            community=self.community, payment_asset=asset,
            head_amount_base_units=50, period=PollTaxPeriod.YEARLY, active=True,
        )
        PollTaxPayment.objects.create(
            poll_tax=pt, person=self.person,
            period_label=pt.current_period_label(), total_paid_base_units=50,
        )
        proposal = make_proposal(self.community, self.person, title="Tax paid")
        self._login()
        url = reverse("assembly:proposal_vote", kwargs={"slug": self.community.slug, "proposal_id": proposal.pk})
        self.client.post(url, {"vote": AssemblyVoteChoice.YES})
        from .models import AssemblyVote
        self.assertTrue(AssemblyVote.objects.filter(proposal=proposal, voter=self.person).exists())

    def test_poll_tax_pay_records_payment(self):
        asset = make_asset("PAY2")
        pt = PollTax.objects.create(
            community=self.community, payment_asset=asset,
            head_amount_base_units=10, period=PollTaxPeriod.YEARLY, active=True,
        )
        self._login()
        url = reverse("assembly:poll_tax_pay", kwargs={"slug": self.community.slug, "poll_tax_id": pt.pk})
        self.client.post(url)
        self.assertTrue(
            PollTaxPayment.objects.filter(
                poll_tax=pt, person=self.person, period_label=pt.current_period_label()
            ).exists()
        )

    def test_poll_tax_pay_idempotent(self):
        asset = make_asset("PAY3")
        pt = PollTax.objects.create(
            community=self.community, payment_asset=asset,
            head_amount_base_units=10, period=PollTaxPeriod.YEARLY, active=True,
        )
        self._login()
        url = reverse("assembly:poll_tax_pay", kwargs={"slug": self.community.slug, "poll_tax_id": pt.pk})
        self.client.post(url)
        self.client.post(url)
        self.assertEqual(
            PollTaxPayment.objects.filter(poll_tax=pt, person=self.person).count(), 1
        )
