from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction as db_transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from toto.socialhub.models import Community
from toto.ui import PageProcessor

from datetime import timedelta

from .models import (
    AssemblyDecision,
    AssemblyProposal,
    AssemblyProposalType,
    AssemblyStatus,
    AssemblyVote,
    AssemblyVoteChoice,
    CommunityAssemblyConfig,
    CommunitySenate,
    CommunityRule,
    CommunityTransactionFee,
    PollTax,
    PollTaxPayment,
    SenateVeto,
)
from .queries import community_assembly_summaries, fee_current_chart_data, fee_history_chart_data, overview_stats


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


@login_required
def assembly_overview(request):
    from toto.people.civic import is_committed_citizen
    person = getattr(request.user, "community_profile", None)
    is_broad_access = bool(
        person and (
            is_committed_citizen(person)
            or person.communities.filter(is_federal_tribe=True).exists()
        )
    )
    member_community_pks = (
        list(person.communities.values_list("pk", flat=True)) if person else []
    )
    return _render(request, "assembly/overview.html", {
        "stats": overview_stats(),
        "communities": community_assembly_summaries(),
        "fee_current_data": json.loads(fee_current_chart_data()),
        "fee_history_data": json.loads(fee_history_chart_data()),
        "member_community_pks": member_community_pks,
        "is_federal_agent_overview": is_broad_access,
    })


def _require_person(request):
    person = getattr(request.user, "community_profile", None)
    if not person:
        raise PermissionDenied
    return person


def _require_member(request, community):
    person = _require_person(request)
    if not community.members.filter(pk=person.pk).exists():
        raise PermissionDenied
    return person


def _is_poll_tax_exempt(person) -> bool:
    from toto.people.civic import is_committed_citizen
    if is_committed_citizen(person):
        return True
    if person.communities.filter(is_federal_tribe=True).exists():
        return True
    return False


def _is_senator(person, community) -> bool:
    """Senators are the community's senior members when the assembly is bicameral."""
    try:
        if not community.assembly_config.is_bicameral:
            return False
    except CommunityAssemblyConfig.DoesNotExist:
        return False
    return community.senior_members.filter(pk=person.pk).exists()


def _has_voting_rights(person, community) -> bool:
    if _is_poll_tax_exempt(person):
        return True
    active_taxes = PollTax.objects.filter(community=community, active=True)
    if not active_taxes.exists():
        return True
    for pt in active_taxes:
        label = pt.current_period_label()
        if not PollTaxPayment.objects.filter(poll_tax=pt, person=person, period_label=label).exists():
            return False
    return True


def _get_quorum_fraction(community) -> float:
    try:
        return float(community.assembly_config.quorum_fraction)
    except CommunityAssemblyConfig.DoesNotExist:
        return 0.51


def _route_passed_proposal(proposal: AssemblyProposal) -> str:
    """
    After a proposal clears the popular vote quorum, check whether the community
    is bicameral. If yes, park the proposal for senate review; otherwise enact
    immediately.  Returns 'enacted' or 'pending_senate'.
    """
    try:
        if proposal.community.assembly_config.is_bicameral:
            veto_window = 7
            try:
                veto_window = proposal.community.senate.veto_window_days
            except CommunitySenate.DoesNotExist:
                pass
            proposal.status = AssemblyStatus.PENDING_SENATE
            proposal.senate_deadline = timezone.now() + timedelta(days=veto_window)
            proposal.save(update_fields=["status", "senate_deadline"])
            return "pending_senate"
    except CommunityAssemblyConfig.DoesNotExist:
        pass
    _enact_proposal(proposal)
    return "enacted"


def _enact_proposal(proposal: AssemblyProposal) -> AssemblyDecision:
    """Mark proposal passed and create an immutable decision record + downstream objects."""
    proposal.status = AssemblyStatus.PASSED
    proposal.save(update_fields=["status"])

    content = {
        "proposal_id": proposal.pk,
        "proposal_type": proposal.proposal_type,
        "is_repeal": proposal.is_repeal,
        "title": proposal.title,
        "body": proposal.body,
        "tally": proposal.tally(),
        "enacted_at": timezone.now().isoformat(),
        "metadata": proposal.metadata,
    }

    decision = AssemblyDecision.record(
        community=proposal.community,
        proposal=proposal,
        decision_type=proposal.proposal_type,
        title=proposal.title,
        content=content,
    )

    if proposal.proposal_type == AssemblyProposalType.RULE:
        if proposal.is_repeal:
            # Deactivate matching active rules by title
            CommunityRule.objects.filter(
                community=proposal.community, title=proposal.title, active=True
            ).update(active=False)
        else:
            CommunityRule.objects.create(
                community=proposal.community,
                decision=decision,
                title=proposal.title,
                text=proposal.body,
                active=True,
            )
    elif proposal.proposal_type == AssemblyProposalType.ASSET_TAX:
        fee_bps = int(proposal.metadata.get("fee_bps", 0))
        asset_id = proposal.metadata.get("asset_id")
        from toto.assets.models import Asset
        asset = Asset.objects.filter(pk=asset_id).first() if asset_id else None
        # Deactivate any prior fee for the same asset in this community
        CommunityTransactionFee.objects.filter(
            community=proposal.community, asset=asset, active=True
        ).update(active=False)
        CommunityTransactionFee.objects.create(
            community=proposal.community,
            asset=asset,
            fee_bps=fee_bps,
            set_by=CommunityTransactionFee.SET_BY_ASSEMBLY,
            decision=decision,
            active=True,
        )

    # Magistrate election — confirmed via separate magistrate:elect_confirm action
    # Magistrate impeachment — immediately updates the seat status
    elif proposal.proposal_type == AssemblyProposalType.IMPEACHMENT:
        mag_id = proposal.metadata.get("magistrate_id")
        if mag_id:
            try:
                from toto.magistrate.models import Magistrate as _Magistrate
                _Magistrate.objects.filter(
                    pk=mag_id, status__in=["active", "suspended"]
                ).update(status="impeached")
            except Exception:
                pass

    elif proposal.proposal_type == AssemblyProposalType.SENATE_APPOINTMENT:
        nominee_id = proposal.metadata.get("nominee_id")
        if nominee_id:
            try:
                from toto.people.models import Person as _Person
                nominee = _Person.objects.get(pk=nominee_id)
                proposal.community.senior_members.add(nominee)
            except Exception:
                pass

    elif proposal.proposal_type == AssemblyProposalType.MOBILIZATION:
        try:
            from toto.mobilization.models import MobilizationEvent
            MobilizationEvent.objects.create(
                community=proposal.community,
                title=proposal.title,
                description=proposal.body,
                status="active",
            )
        except Exception:
            pass

    return decision


@login_required
def community_assembly(request, slug):
    community = get_object_or_404(Community, slug=slug)
    person = getattr(request.user, "community_profile", None)

    from toto.people.civic import is_committed_citizen
    is_member = bool(person and community.members.filter(pk=person.pk).exists())
    is_committed = bool(person and is_committed_citizen(person))
    is_federal_tribe_broad = bool(person and person.communities.filter(is_federal_tribe=True).exists())
    is_federal_agent = bool(person and getattr(person, "is_federal_agent", False))

    if not is_member and not is_committed and not is_federal_tribe_broad:
        messages.error(request, "You are not a member of this community's assembly.")
        return redirect("capitol:overview")

    is_read_only = not is_member

    proposals = (
        AssemblyProposal.objects
        .filter(community=community)
        .prefetch_related("votes")
        .select_related("opened_by")
        .order_by("-created_at")[:40]
    )

    proposal_data = []
    for p in proposals:
        tally = p.tally()
        user_vote = p.votes.filter(voter=person).first() if person and not is_read_only else None
        proposal_data.append({
            "proposal": p,
            "tally": tally,
            "total_votes": sum(tally.values()),
            "user_vote": user_vote,
        })

    poll_taxes = []
    if not is_read_only and person:
        raw_poll_taxes = PollTax.objects.filter(community=community, active=True).select_related("payment_asset", "wealth_asset")
        for pt in raw_poll_taxes:
            label = pt.current_period_label()
            paid = PollTaxPayment.objects.filter(poll_tax=pt, person=person, period_label=label).exists()
            amount_owed = pt.compute_amount_for(person)
            poll_taxes.append({"poll_tax": pt, "period_label": label, "paid": paid, "amount_owed": amount_owed})

    import json
    from toto.assets.models import Asset
    from toto.people.models import Person as PersonModel
    is_federal_tribe_member = bool(person and person.communities.filter(is_federal_tribe=True).exists())
    quorum_fraction = _get_quorum_fraction(community)
    available_assets_json = json.dumps(
        list(Asset.objects.order_by("name").values("id", "name", "unit_name"))
    )

    # Magistrate election support
    magistrate_roles = []
    eligible_nominees = []
    passed_elections = []
    try:
        from toto.magistrate.models import MagistrateRole
        magistrate_roles = list(MagistrateRole.objects.order_by("order", "name"))
        eligible_nominees = list(
            PersonModel.objects.filter(is_federal_agent=True)
            .order_by("display_name")
            .values("id", "display_name")
        )
        passed_elections = list(
            AssemblyProposal.objects.filter(
                community=community,
                proposal_type=AssemblyProposalType.MAGISTRATE_ELECTION,
                status=AssemblyStatus.PASSED,
            ).exclude(elected_magistrates__isnull=False)
        )
    except Exception:
        pass

    proposal_type_choices = [
        (val, label) for val, label in AssemblyProposalType.choices
        if val not in (AssemblyProposalType.MAGISTRATE_ELECTION, AssemblyProposalType.IMPEACHMENT)
    ]

    magistrate_decisions = []
    try:
        from toto.magistrate.models import MagistrateDecision
        magistrate_decisions = list(
            MagistrateDecision.objects
            .filter(community=community)
            .select_related("magistrate__person", "magistrate__role", "reviewed_by")
            .order_by("-created_at")[:15]
        )
    except Exception:
        pass

    is_senator = _is_senator(person, community) if person else False
    pending_senate = (
        AssemblyProposal.objects
        .filter(community=community, status=AssemblyStatus.PENDING_SENATE)
        .order_by("senate_deadline")
    ) if not is_senator else []

    return _render(request, "assembly/community_assembly.html", {
        "community": community,
        "person": person,
        "proposal_data": proposal_data,
        "community_rules": CommunityRule.objects.filter(community=community, active=True),
        "community_fees": CommunityTransactionFee.objects.filter(community=community, active=True).select_related("asset").order_by("asset"),
        "poll_taxes": poll_taxes,
        "decisions": AssemblyDecision.objects.filter(community=community).order_by("-created_at")[:20],
        "has_voting_rights": False if is_read_only else _has_voting_rights(person, community),
        "is_senator": is_senator,
        "is_read_only": is_read_only,
        "pending_senate": pending_senate,
        "is_federal_agent": is_federal_agent,
        "is_federal_tribe_member": is_federal_tribe_member,
        "quorum_fraction_pct": int(quorum_fraction * 100),
        "assembly_proposal_types": proposal_type_choices,
        "vote_choices": AssemblyVoteChoice.choices,
        "available_assets_json": available_assets_json,
        "magistrate_roles": magistrate_roles,
        "eligible_nominees": eligible_nominees,
        "passed_elections": passed_elections,
        "magistrate_decisions": magistrate_decisions,
    })


@login_required
@require_POST
def proposal_create(request, slug):
    community = get_object_or_404(Community, slug=slug)
    person = _require_member(request, community)

    title = request.POST.get("title", "").strip()
    body = request.POST.get("body", "").strip()
    proposal_type = request.POST.get("proposal_type", "")

    if not title or proposal_type not in AssemblyProposalType.values:
        messages.error(request, "Title and valid proposal type are required.")
        return redirect("assembly:community_assembly", slug=slug)

    closes_date = request.POST.get("closes_date", "").strip()
    closes_time = request.POST.get("closes_time", "").strip() or "23:59"
    closes_at = None
    if closes_date:
        from django.utils.dateparse import parse_datetime
        closes_at = parse_datetime(f"{closes_date}T{closes_time}:00")
    is_repeal = request.POST.get("is_repeal") == "1"

    metadata = {}
    if proposal_type == AssemblyProposalType.ASSET_TAX:
        metadata["fee_bps"] = int(request.POST.get("fee_bps", 0) or 0)
        metadata["asset_id"] = request.POST.get("asset_id", "").strip() or None

    AssemblyProposal.objects.create(
        community=community,
        proposal_type=proposal_type,
        is_repeal=is_repeal,
        title=title,
        body=body,
        status=AssemblyStatus.OPEN,
        opened_by=person,
        opens_at=timezone.now(),
        closes_at=closes_at,
        metadata=metadata,
    )
    messages.success(request, f'Proposal "{title}" opened for voting.')
    return redirect("assembly:community_assembly", slug=slug)


@require_POST
@login_required
def proposal_vote(request, slug, proposal_id):
    community = get_object_or_404(Community, slug=slug)
    person = _require_member(request, community)
    proposal = get_object_or_404(AssemblyProposal, pk=proposal_id, community=community)

    if not proposal.is_open:
        messages.error(request, "This proposal is no longer open for voting.")
        return redirect("assembly:community_assembly", slug=slug)

    if not _has_voting_rights(person, community):
        messages.error(request, "Pay the poll tax for the current period to unlock voting rights.")
        return redirect("assembly:community_assembly", slug=slug)

    if AssemblyVote.objects.filter(proposal=proposal, voter=person).exists():
        messages.error(request, "You have already voted on this proposal.")
        return redirect("assembly:community_assembly", slug=slug)

    vote_value = request.POST.get("vote", "")
    if vote_value not in AssemblyVoteChoice.values:
        messages.error(request, "Invalid vote.")
        return redirect("assembly:community_assembly", slug=slug)

    quorum_fraction = _get_quorum_fraction(community)

    with db_transaction.atomic():
        AssemblyVote.objects.create(proposal=proposal, voter=person, vote=vote_value)
        tally = proposal.tally()
        yes = tally[AssemblyVoteChoice.YES]
        no = tally[AssemblyVoteChoice.NO]
        total = yes + no
        if total > 0 and (yes / total) >= quorum_fraction:
            outcome = _route_passed_proposal(proposal)
            if outcome == "pending_senate":
                messages.success(request, "Proposal passed the popular vote — now awaiting senate review.")
            else:
                messages.success(request, "Proposal passed and enacted.")
            return redirect("assembly:community_assembly", slug=slug)
        if total > 0 and (no / total) > (1 - quorum_fraction):
            proposal.status = AssemblyStatus.REJECTED
            proposal.save(update_fields=["status"])
            messages.info(request, "Proposal cannot reach quorum — rejected.")
            return redirect("assembly:community_assembly", slug=slug)

    messages.success(request, f'Vote "{vote_value}" recorded.')
    return redirect("assembly:community_assembly", slug=slug)


@require_POST
@login_required
def proposal_close(request, slug, proposal_id):
    community = get_object_or_404(Community, slug=slug)
    _require_member(request, community)
    if not request.user.is_staff:
        raise PermissionDenied
    proposal = get_object_or_404(AssemblyProposal, pk=proposal_id, community=community)
    quorum_fraction = _get_quorum_fraction(community)
    with db_transaction.atomic():
        tally = proposal.tally()
        yes = tally[AssemblyVoteChoice.YES]
        no = tally[AssemblyVoteChoice.NO]
        total = yes + no
        if total > 0 and (yes / total) >= quorum_fraction:
            outcome = _route_passed_proposal(proposal)
            if outcome == "pending_senate":
                messages.success(request, "Proposal closed — passed popular vote, now awaiting senate review.")
            else:
                messages.success(request, "Proposal closed — passed and enacted.")
        else:
            proposal.status = AssemblyStatus.REJECTED
            proposal.save(update_fields=["status"])
            messages.info(request, "Proposal closed — rejected.")
    return redirect("assembly:community_assembly", slug=slug)


@require_POST
@login_required
def poll_tax_pay(request, slug, poll_tax_id):
    community = get_object_or_404(Community, slug=slug)
    person = _require_member(request, community)

    if _is_poll_tax_exempt(person):
        messages.info(request, "You are exempt from poll taxes.")
        return redirect("assembly:community_assembly", slug=slug)

    poll_tax = get_object_or_404(PollTax, pk=poll_tax_id, community=community, active=True)
    label = poll_tax.current_period_label()

    if PollTaxPayment.objects.filter(poll_tax=poll_tax, person=person, period_label=label).exists():
        messages.info(request, "Already paid for this period.")
        return redirect("assembly:community_assembly", slug=slug)

    total = poll_tax.compute_amount_for(person)
    head = poll_tax.head_amount_base_units
    wealth = max(0, total - head)

    PollTaxPayment.objects.create(
        poll_tax=poll_tax,
        person=person,
        period_label=label,
        head_amount_paid_base_units=head,
        wealth_amount_paid_base_units=wealth,
        total_paid_base_units=total,
    )
    messages.success(request, f"Poll tax paid for {label}. Voting rights unlocked.")
    return redirect("assembly:community_assembly", slug=slug)


# ---------------------------------------------------------------------------
# Senate
# ---------------------------------------------------------------------------

@require_POST
@login_required
def senate_nominate(request, slug):
    """Any community member proposes a senate appointment via the popular assembly."""
    community = get_object_or_404(Community, slug=slug)
    person = _require_member(request, community)

    try:
        if not community.assembly_config.is_bicameral:
            messages.error(request, "This community does not have an active senate.")
            return redirect("assembly:community_assembly", slug=slug)
    except CommunityAssemblyConfig.DoesNotExist:
        messages.error(request, "This community does not have an active senate.")
        return redirect("assembly:community_assembly", slug=slug)

    nominee_id = request.POST.get("nominee_id", "").strip()
    closes_date = request.POST.get("closes_date", "").strip()

    from toto.people.models import Person as PersonModel
    nominee = get_object_or_404(PersonModel, pk=nominee_id)

    if community.senior_members.filter(pk=nominee.pk).exists():
        messages.info(request, f"{nominee} is already a senator.")
        return redirect("assembly:community_assembly", slug=slug)

    closes_at = None
    if closes_date:
        from django.utils.dateparse import parse_datetime
        closes_at = parse_datetime(f"{closes_date}T23:59:00")

    AssemblyProposal.objects.create(
        community=community,
        proposal_type=AssemblyProposalType.SENATE_APPOINTMENT,
        title=f"Appoint {nominee} to the Senate",
        body=request.POST.get("reason", f"Proposal to appoint {nominee} as senator of {community}."),
        status=AssemblyStatus.OPEN,
        opened_by=person,
        opens_at=timezone.now(),
        closes_at=closes_at,
        metadata={"nominee_id": str(nominee.pk)},
    )
    messages.success(request, f"Senate appointment proposal for {nominee} submitted for assembly vote.")
    return redirect("assembly:community_assembly", slug=slug)


@require_POST
@login_required
def senate_appoint_federal(request, slug):
    """A federal agent directly seats a senator by adding them to the community's senior members."""
    community = get_object_or_404(Community, slug=slug)
    person = getattr(request.user, "community_profile", None)
    if not person or not getattr(person, "is_federal_agent", False):
        raise PermissionDenied

    try:
        if not community.assembly_config.is_bicameral:
            messages.error(request, "This community does not have an active senate.")
            return redirect("assembly:community_assembly", slug=slug)
    except CommunityAssemblyConfig.DoesNotExist:
        messages.error(request, "This community does not have an active senate.")
        return redirect("assembly:community_assembly", slug=slug)

    nominee_id = request.POST.get("nominee_id", "").strip()

    from toto.people.models import Person as PersonModel
    nominee = get_object_or_404(PersonModel, pk=nominee_id)

    community.senior_members.add(nominee)
    messages.success(request, f"{nominee} seated in the senate by federal authority.")
    return redirect("assembly:senate_review", slug=slug)


def _require_senator(request, community):
    """Person must be a senior member of the community (senator) in a bicameral assembly."""
    person = getattr(request.user, "community_profile", None)
    if not person or not _is_senator(person, community):
        raise PermissionDenied
    return person


@login_required
def senate_review(request, slug):
    """Senate members' view: all proposals awaiting senate review for this community."""
    community = get_object_or_404(Community, slug=slug)
    person = _require_senator(request, community)

    try:
        senate = community.senate
    except CommunitySenate.DoesNotExist:
        messages.error(request, "This community does not have an active senate.")
        return redirect("assembly:community_assembly", slug=slug)

    pending = (
        AssemblyProposal.objects
        .filter(community=community, status=AssemblyStatus.PENDING_SENATE)
        .order_by("senate_deadline")
    )
    vetoed = (
        AssemblyProposal.objects
        .filter(community=community, status=AssemblyStatus.VETOED)
        .select_related("senate_veto__senator")
        .order_by("-senate_veto__vetoed_at")[:20]
    )

    return render(request, "assembly/senate_review.html", PageProcessor().decorate({
        "community": community,
        "senate": senate,
        "person": person,
        "pending_proposals": pending,
        "vetoed_proposals": vetoed,
        "now": timezone.now(),
    }, request))


@require_POST
@login_required
def senate_veto(request, slug, proposal_id):
    """A senator blocks a proposal that passed the popular assembly."""
    community = get_object_or_404(Community, slug=slug)
    person = _require_senator(request, community)

    proposal = get_object_or_404(
        AssemblyProposal,
        pk=proposal_id,
        community=community,
        status=AssemblyStatus.PENDING_SENATE,
    )

    if proposal.senate_deadline and timezone.now() > proposal.senate_deadline:
        messages.error(request, "The senate veto window has expired — this proposal can no longer be blocked.")
        return redirect("assembly:senate_review", slug=slug)

    if SenateVeto.objects.filter(proposal=proposal).exists():
        messages.info(request, "This proposal has already been vetoed.")
        return redirect("assembly:senate_review", slug=slug)

    reason = request.POST.get("reason", "").strip()

    with db_transaction.atomic():
        SenateVeto.objects.create(proposal=proposal, senator=person, reason=reason)
        proposal.status = AssemblyStatus.VETOED
        proposal.save(update_fields=["status"])

    messages.success(request, f"Senate veto recorded. '{proposal.title}' will not be enacted.")
    return redirect("assembly:senate_review", slug=slug)


@require_POST
@login_required
def senate_confirm(request, slug, proposal_id):
    """
    After the veto window expires with no veto, any community member can trigger
    enactment. Acts as the 'clock ran out, senate did not block' confirmation.
    """
    community = get_object_or_404(Community, slug=slug)
    _require_member(request, community)

    proposal = get_object_or_404(
        AssemblyProposal,
        pk=proposal_id,
        community=community,
        status=AssemblyStatus.PENDING_SENATE,
    )

    if not proposal.senate_deadline or timezone.now() <= proposal.senate_deadline:
        messages.error(request, "The senate review window has not yet expired.")
        return redirect("assembly:community_assembly", slug=slug)

    if SenateVeto.objects.filter(proposal=proposal).exists():
        messages.error(request, "This proposal was vetoed by the senate.")
        return redirect("assembly:community_assembly", slug=slug)

    _enact_proposal(proposal)
    messages.success(request, f"Senate window expired without veto — '{proposal.title}' enacted.")
    return redirect("assembly:community_assembly", slug=slug)
