from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from toto.assembly.models import (
    AssemblyProposal,
    AssemblyStatus,
    CommunityAssemblyConfig,
    CommunitySenate,
    SenateVeto,
)
from toto.socialhub.models import Community
from toto.ui import PageProcessor


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _is_senator(person, community) -> bool:
    if not person:
        return False
    try:
        if not community.assembly_config.is_bicameral:
            return False
    except CommunityAssemblyConfig.DoesNotExist:
        return False
    return community.senior_members.filter(pk=person.pk).exists()


@login_required
def senate_overview(request):
    bicameral = []
    for c in Community.objects.order_by("name"):
        try:
            cfg = c.assembly_config
            if not cfg.is_bicameral:
                continue
        except CommunityAssemblyConfig.DoesNotExist:
            continue
        senators = list(c.senior_members.order_by("display_name"))
        pending_count = AssemblyProposal.objects.filter(
            community=c, status=AssemblyStatus.PENDING_SENATE
        ).count()
        try:
            senate = c.senate
            veto_window = senate.veto_window_days
        except CommunitySenate.DoesNotExist:
            veto_window = 7
        bicameral.append({
            "community": c,
            "senators": senators,
            "senator_count": len(senators),
            "pending_count": pending_count,
            "veto_window": veto_window,
        })
    return _render(request, "senate/overview.html", {"bicameral": bicameral})


@login_required
def community_senate(request, slug):
    community = get_object_or_404(Community, slug=slug)
    person = getattr(request.user, "community_profile", None)

    is_bicameral = False
    try:
        is_bicameral = community.assembly_config.is_bicameral
    except CommunityAssemblyConfig.DoesNotExist:
        pass

    senate = None
    if is_bicameral:
        try:
            senate = community.senate
        except CommunitySenate.DoesNotExist:
            pass

    senators = list(community.senior_members.order_by("display_name")) if is_bicameral else []
    is_senator = _is_senator(person, community)
    is_federal_agent = bool(person and getattr(person, "is_federal_agent", False))

    pending = (
        AssemblyProposal.objects
        .filter(community=community, status=AssemblyStatus.PENDING_SENATE)
        .order_by("senate_deadline")
    ) if is_bicameral else []

    vetoed = (
        AssemblyProposal.objects
        .filter(community=community, status=AssemblyStatus.VETOED)
        .select_related("senate_veto__senator")
        .order_by("-senate_veto__vetoed_at")[:30]
    ) if is_bicameral else []

    from toto.people.models import Person as PersonModel
    eligible_nominees = []
    if is_federal_agent and is_bicameral:
        eligible_nominees = list(
            PersonModel.objects.order_by("display_name").values("id", "display_name")
        )

    return _render(request, "senate/community_senate.html", {
        "community": community,
        "is_bicameral": is_bicameral,
        "senate": senate,
        "senators": senators,
        "is_senator": is_senator,
        "is_federal_agent": is_federal_agent,
        "pending_proposals": pending,
        "vetoed_proposals": vetoed,
        "eligible_nominees": eligible_nominees,
        "now": timezone.now(),
    })
