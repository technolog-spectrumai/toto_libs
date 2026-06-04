from __future__ import annotations

import json
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from toto.socialhub.models import Community
from toto.ui import PageProcessor


from .community_session import set_community as _set_community


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _magistrate_stats():
    from toto.magistrate.models import AssetFreeze, Magistrate, MagistrateDecision, MagistrateFine, MagistrateReport, MagistrateRole

    active_count = Magistrate.objects.filter(status="active").count()
    role_count = MagistrateRole.objects.count()
    decision_count = MagistrateDecision.objects.count()
    report_count = MagistrateReport.objects.count()
    fine_count = MagistrateFine.objects.count()
    freeze_count = AssetFreeze.objects.filter(status=AssetFreeze.STATUS_ACTIVE).count()
    return {
        "active_magistrates": active_count,
        "magistrate_roles": role_count,
        "magistrate_decisions": decision_count,
        "magistrate_reports": report_count,
        "magistrate_fines": fine_count,
        "active_freezes": freeze_count,
    }


def _assembly_stats():
    from toto.assembly.models import AssemblyDecision, AssemblyProposal, AssemblyStatus, CommunityRule

    total_proposals = AssemblyProposal.objects.count()
    voting_proposals = AssemblyProposal.objects.filter(status=AssemblyStatus.OPEN).count()
    enacted = AssemblyProposal.objects.filter(status=AssemblyStatus.PASSED).count()
    decision_count = AssemblyDecision.objects.count()
    rule_count = CommunityRule.objects.count()

    by_status = list(
        AssemblyProposal.objects
        .values("status")
        .annotate(n=Count("id"))
        .order_by("-n")
    )
    return {
        "total_proposals": total_proposals,
        "voting_proposals": voting_proposals,
        "enacted_proposals": enacted,
        "assembly_decisions": decision_count,
        "community_rules": rule_count,
        "proposals_by_status": by_status,
    }


def _tribunal_stats():
    from toto.tribunal.models import JurySession, TribunalCase, TribunalRuling

    total_cases = TribunalCase.objects.count()
    open_cases = TribunalCase.objects.filter(status="open").count()
    resolved_cases = TribunalCase.objects.filter(status="resolved").count()
    ruling_count = TribunalRuling.objects.count()
    jury_count = JurySession.objects.count()

    by_status = list(
        TribunalCase.objects
        .values("status")
        .annotate(n=Count("id"))
        .order_by("-n")
    )
    return {
        "total_cases": total_cases,
        "open_cases": open_cases,
        "resolved_cases": resolved_cases,
        "tribunal_rulings": ruling_count,
        "jury_sessions": jury_count,
        "cases_by_status": by_status,
    }


def _treasury_stats():
    from toto.socialhub.models import Community

    community_count = Community.objects.count()
    return {
        "treasury_communities": community_count,
    }


def _activity_series():
    """Last 30 days of magistrate decisions + assembly proposals + tribunal cases."""
    from toto.assembly.models import AssemblyProposal
    from toto.magistrate.models import MagistrateDecision
    from toto.tribunal.models import TribunalCase

    since = timezone.now() - timedelta(days=30)
    decisions = list(
        MagistrateDecision.objects
        .filter(created_at__gte=since)
        .extra(select={"day": "date(created_at)"})
        .values("day")
        .annotate(n=Count("id"))
        .order_by("day")
    )
    proposals = list(
        AssemblyProposal.objects
        .filter(created_at__gte=since)
        .extra(select={"day": "date(created_at)"})
        .values("day")
        .annotate(n=Count("id"))
        .order_by("day")
    )
    cases = list(
        TribunalCase.objects
        .filter(opened_at__gte=since)
        .extra(select={"day": "date(opened_at)"})
        .values("day")
        .annotate(n=Count("id"))
        .order_by("day")
    )

    def to_dict(series):
        return {str(row["day"]): row["n"] for row in series}

    all_days = sorted(set(
        list(to_dict(decisions)) + list(to_dict(proposals)) + list(to_dict(cases))
    ))
    if not all_days:
        return []

    d_map = to_dict(decisions)
    p_map = to_dict(proposals)
    c_map = to_dict(cases)
    return [
        {"date": day, "decisions": d_map.get(day, 0), "proposals": p_map.get(day, 0), "cases": c_map.get(day, 0)}
        for day in all_days
    ]


@login_required
@require_POST
def set_community(request):
    slug = request.POST.get("community", "").strip()
    section = request.POST.get("section", "capitol")
    if slug and Community.objects.filter(slug=slug).exists():
        _set_community(request.user.pk, slug)
        if section == "assembly":
            return redirect("assembly:community_assembly", slug=slug)
        if section == "senate":
            return redirect("senate:community_senate", slug=slug)
        if section == "treasury":
            community = Community.objects.get(slug=slug)
            return redirect("treasury:detail", pk=community.pk)
    next_url = request.POST.get("next") or reverse("capitol:overview")
    return redirect(next_url)


@login_required
def overview(request):
    mag = _magistrate_stats()
    asm = _assembly_stats()
    trib = _tribunal_stats()
    tres = _treasury_stats()

    activity = _activity_series()

    context = {
        **mag,
        **asm,
        **trib,
        **tres,
        "activity_series": json.dumps(activity),
        "proposals_by_status_json": json.dumps(asm["proposals_by_status"]),
        "cases_by_status_json": json.dumps(trib["cases_by_status"]),
    }
    return _render(request, "capitol/overview.html", context)
