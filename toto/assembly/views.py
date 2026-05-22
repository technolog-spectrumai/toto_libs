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

from .models import (
    AssemblyDecision,
    AssemblyProposal,
    AssemblyProposalType,
    AssemblyStatus,
    AssemblyVote,
    AssemblyVoteChoice,
    CommunityAssemblyConfig,
    CommunityRule,
    CommunityTransactionFee,
    PollTax,
    PollTaxPayment,
)
from .queries import community_assembly_summaries, fee_current_chart_data, fee_history_chart_data, overview_stats


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


@login_required
def assembly_overview(request):
    return _render(request, "assembly/overview.html", {
        "stats": overview_stats(),
        "communities": community_assembly_summaries(),
        "fee_current_data": json.loads(fee_current_chart_data()),
        "fee_history_data": json.loads(fee_history_chart_data()),
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
    if getattr(person, "is_federal_agent", False):
        return True
    if person.communities.filter(is_federal_tribe=True).exists():
        return True
    return False


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

    return decision


@login_required
def community_assembly(request, slug):
    community = get_object_or_404(Community, slug=slug)
    person = _require_member(request, community)

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
        user_vote = p.votes.filter(voter=person).first()
        proposal_data.append({
            "proposal": p,
            "tally": tally,
            "total_votes": sum(tally.values()),
            "user_vote": user_vote,
        })

    raw_poll_taxes = PollTax.objects.filter(community=community, active=True).select_related("payment_asset", "wealth_asset")
    poll_taxes = []
    for pt in raw_poll_taxes:
        label = pt.current_period_label()
        paid = PollTaxPayment.objects.filter(poll_tax=pt, person=person, period_label=label).exists()
        amount_owed = pt.compute_amount_for(person)
        poll_taxes.append({"poll_tax": pt, "period_label": label, "paid": paid, "amount_owed": amount_owed})

    import json
    from toto.assets.models import Asset
    is_federal_agent = getattr(person, "is_federal_agent", False)
    is_federal_tribe_member = person.communities.filter(is_federal_tribe=True).exists()
    quorum_fraction = _get_quorum_fraction(community)
    available_assets_json = json.dumps(
        list(Asset.objects.order_by("name").values("id", "name", "unit_name"))
    )

    return _render(request, "assembly/community_assembly.html", {
        "community": community,
        "person": person,
        "proposal_data": proposal_data,
        "community_rules": CommunityRule.objects.filter(community=community, active=True),
        "community_fees": CommunityTransactionFee.objects.filter(community=community, active=True).select_related("asset").order_by("asset"),
        "poll_taxes": poll_taxes,
        "decisions": AssemblyDecision.objects.filter(community=community).order_by("-created_at")[:20],
        "has_voting_rights": _has_voting_rights(person, community),
        "is_federal_agent": is_federal_agent,
        "is_federal_tribe_member": is_federal_tribe_member,
        "quorum_fraction_pct": int(quorum_fraction * 100),
        "assembly_proposal_types": AssemblyProposalType.choices,
        "vote_choices": AssemblyVoteChoice.choices,
        "available_assets_json": available_assets_json,
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
            _enact_proposal(proposal)
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
            _enact_proposal(proposal)
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
