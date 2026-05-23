from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from toto.assembly.models import AssemblyProposal, AssemblyProposalType, AssemblyStatus
from toto.socialhub.models import Community
from toto.ui import PageProcessor

from .models import Magistrate, MagistrateDecision, MagistrateReport, MagistrateRole
from .queries import overview_stats, reports_by_status_chart_data, seats_by_role_chart_data, seats_by_status_chart_data


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _person(request):
    person = getattr(request.user, "community_profile", None)
    if not person:
        raise PermissionDenied
    return person


# ---------------------------------------------------------------------------
# Overview / list
# ---------------------------------------------------------------------------

@login_required
def overview(request):
    import json

    magistrates = (
        Magistrate.objects
        .select_related("person", "role", "community", "source_proposal")
        .order_by("community__name", "role__order", "-elected_at")
    )

    by_community: dict = {}
    for m in magistrates:
        by_community.setdefault(m.community, []).append(m)

    return _render(request, "magistrate/overview.html", {
        "by_community": by_community,
        "stats": overview_stats(),
        "seats_by_role_data": json.loads(seats_by_role_chart_data()),
        "seats_by_status_data": json.loads(seats_by_status_chart_data()),
        "reports_by_status_data": json.loads(reports_by_status_chart_data()),
    })


@login_required
def role_list(request):
    from django.db.models import Count, Q
    roles = MagistrateRole.objects.annotate(
        active_count=Count("holders", filter=Q(holders__status="active"))
    )
    return _render(request, "magistrate/role_list.html", {"roles": roles})


# ---------------------------------------------------------------------------
# Magistrate detail
# ---------------------------------------------------------------------------

@login_required
def magistrate_detail(request, pk):
    mag = get_object_or_404(
        Magistrate.objects.select_related("person", "role", "community", "source_proposal"),
        pk=pk,
    )
    reports = mag.reports.select_related("acknowledged_by").order_by("-created_at")
    person = _person(request)
    community = mag.community
    can_impeach = (
        mag.is_active
        and mag.person != person
        and community.members.filter(pk=person.pk).exists()
    )

    return _render(request, "magistrate/magistrate_detail.html", {
        "mag": mag,
        "reports": reports,
        "person": person,
        "can_report": mag.person == person,
        "can_acknowledge": person != mag.person,
        "can_impeach": can_impeach,
    })


# ---------------------------------------------------------------------------
# Command console (power view — magistrate-only)
# ---------------------------------------------------------------------------

def _collect_role_stats(mag):
    stats = {}
    role = mag.role
    community = mag.community

    if role.overseeing_mobilization:
        try:
            from toto.mobilization.models import Deployment
            stats["active_deployments"] = Deployment.objects.filter(status="active").count()
        except Exception:
            pass

    if role.overseeing_tribunal:
        try:
            from toto.tribunal.models import TribunalCase
            stats["active_tribunal_cases"] = TribunalCase.objects.filter(
                status__in=["open", "in_progress"]
            ).count()
        except Exception:
            pass

    if role.overseeing_public_order:
        try:
            from toto.detections.models import Detection
            stats["active_detections"] = Detection.objects.filter(status="active").count()
        except Exception:
            pass

    if role.overseeing_finance or role.overseeing_legislation:
        try:
            from toto.assembly.models import (
                AssemblyProposal, AssemblyStatus,
                CommunityTransactionFee, PollTax,
            )
            if role.overseeing_finance:
                stats["active_fees"] = CommunityTransactionFee.objects.filter(
                    community=community, active=True
                ).count()
                stats["active_poll_taxes"] = PollTax.objects.filter(
                    community=community, active=True
                ).count()
            if role.overseeing_legislation:
                stats["open_proposals"] = AssemblyProposal.objects.filter(
                    community=community, status=AssemblyStatus.OPEN
                ).count()
        except Exception:
            pass

    return stats


def _domain_actions(role):
    actions = []
    if role.overseeing_mobilization:
        actions += [
            ("mobilization_call",  "Mobilization Call",       "fa-solid fa-shield-halved",       "warn"),
            ("emergency_declare",  "Emergency Declaration",   "fa-solid fa-triangle-exclamation", "warn"),
        ]
    if role.overseeing_tribunal:
        actions.append(("tribunal_order", "Tribunal Order", "fa-solid fa-gavel", "accent"))
    if role.overseeing_trade:
        actions.append(("trade_order", "Trade Order", "fa-solid fa-store", "success"))
        actions.append(("trade_reversal", "Trade Reversal Order", "fa-solid fa-rotate-left", "warn"))
    if role.overseeing_merchandise:
        actions.append(("merchandise_fine", "Merchandise Quality Fine", "fa-solid fa-magnifying-glass", "caution"))
    if role.overseeing_finance:
        actions.append(("finance_directive", "Finance Directive", "fa-solid fa-coins", "success"))
    if role.overseeing_public_order:
        actions.append(("public_order_directive", "Public Order Directive", "fa-solid fa-shield-cat", "caution"))
    if role.overseeing_legislation:
        actions.append(("legislation_fast_track", "Legislation Fast-Track", "fa-solid fa-feather-pointed", "accent"))
    if role.overseeing_education:
        actions.append(("education_directive", "Education Directive", "fa-solid fa-graduation-cap", "accent"))
    if role.overseeing_relations:
        actions.append(("relations_directive", "Relations Directive", "fa-solid fa-handshake", "success"))
    if role.overseeing_logistics:
        actions.append(("logistics_order", "Logistics Order", "fa-solid fa-truck-fast", "caution"))
    if role.overseeing_interior:
        actions.append(("interior_directive", "Interior Directive", "fa-solid fa-compass", "accent"))
    if role.overseeing_productivity:
        actions.append(("productivity_directive", "Productivity Directive", "fa-solid fa-briefcase", "success"))
    actions.append(("general", "General Directive", "fa-solid fa-scroll", "accent"))
    return actions


@login_required
def magistrate_dashboard(request, pk):
    mag = get_object_or_404(
        Magistrate.objects.select_related("person", "role", "community"),
        pk=pk,
    )
    person = _person(request)
    if mag.person != person:
        raise PermissionDenied

    decisions = (
        MagistrateDecision.objects
        .filter(magistrate=mag)
        .select_related("reviewed_by")
        .order_by("-created_at")
    )

    days_remaining = None
    if mag.term_end:
        days_remaining = max(0, (mag.term_end - timezone.now().date()).days)

    return _render(request, "magistrate/my_dashboard.html", {
        "mag": mag,
        "decisions": decisions[:25],
        "role_stats": _collect_role_stats(mag),
        "domain_actions": _domain_actions(mag.role),
        "active_decisions_count": decisions.filter(status="active").count(),
        "days_remaining": days_remaining,
        "reports_pending": mag.reports.filter(status="submitted").count(),
    })


@login_required
@require_POST
def make_decision(request, pk):
    mag = get_object_or_404(Magistrate, pk=pk)
    person = _person(request)
    if mag.person != person:
        raise PermissionDenied
    if not mag.is_active:
        messages.error(request, "Your term has ended or you are not currently active.")
        return redirect("magistrate:my_dashboard", pk=pk)

    decision_type = request.POST.get("decision_type", "general")
    title = request.POST.get("title", "").strip()
    body = request.POST.get("body", "").strip()

    if not title:
        messages.error(request, "Decision title is required.")
        return redirect("magistrate:my_dashboard", pk=pk)

    valid_types = [t for t, _ in MagistrateDecision.DECISION_TYPES]
    if decision_type not in valid_types:
        decision_type = "general"

    MagistrateDecision.objects.create(
        magistrate=mag,
        community=mag.community,
        decision_type=decision_type,
        title=title,
        body=body,
    )
    messages.success(request, f'Decision "{title}" entered in the ledger. The assembly may review it.')
    return redirect("magistrate:my_dashboard", pk=pk)


@login_required
@require_POST
def revoke_decision(request, decision_pk):
    decision = get_object_or_404(MagistrateDecision, pk=decision_pk)
    person = _person(request)
    if decision.magistrate.person != person:
        raise PermissionDenied
    if decision.status != "active":
        messages.info(request, "This decision is not active.")
        return redirect("magistrate:my_dashboard", pk=decision.magistrate_id)
    decision.status = "revoked"
    decision.save(update_fields=["status", "updated_at"])
    messages.success(request, f'Decision "{decision.title}" revoked.')
    return redirect("magistrate:my_dashboard", pk=decision.magistrate_id)


@login_required
@require_POST
def review_decision(request, decision_pk):
    decision = get_object_or_404(
        MagistrateDecision.objects.select_related("magistrate__person", "community"),
        pk=decision_pk,
    )
    person = _person(request)
    community = decision.community
    if not community.members.filter(pk=person.pk).exists():
        raise PermissionDenied
    if decision.magistrate.person == person:
        messages.error(request, "You cannot review your own decisions.")
        return redirect("assembly:community_assembly", slug=community.slug)
    if decision.status != "active":
        messages.info(request, "Only active decisions can be marked as reviewed.")
        return redirect("assembly:community_assembly", slug=community.slug)
    decision.status = "reviewed"
    decision.reviewed_by = person
    decision.reviewed_at = timezone.now()
    decision.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
    messages.success(request, f'Decision "{decision.title}" acknowledged by assembly.')
    return redirect("assembly:community_assembly", slug=community.slug)


@login_required
@require_POST
def impeach_propose(request, pk):
    mag = get_object_or_404(
        Magistrate.objects.select_related("community", "person", "role"), pk=pk
    )
    person = _person(request)
    community = mag.community

    if not community.members.filter(pk=person.pk).exists():
        raise PermissionDenied
    if mag.person == person:
        messages.error(request, "You cannot propose your own impeachment.")
        return redirect("magistrate:detail", pk=pk)
    if not mag.is_active:
        messages.error(request, "This magistrate is not currently active.")
        return redirect("magistrate:detail", pk=pk)

    reason = request.POST.get("reason", "").strip()
    closes_date = request.POST.get("closes_date", "").strip()
    closes_at = None
    if closes_date:
        from django.utils.dateparse import parse_datetime
        closes_at = parse_datetime(f"{closes_date}T23:59:00")

    from toto.assembly.models import AssemblyProposal, AssemblyProposalType, AssemblyStatus
    AssemblyProposal.objects.create(
        community=community,
        proposal_type=AssemblyProposalType.IMPEACHMENT,
        title=f"Impeach {mag.person} ({mag.role.name})",
        body=reason or f"Proposal to remove {mag.person} from the office of {mag.role.name} in {community}.",
        status=AssemblyStatus.OPEN,
        opened_by=person,
        opens_at=timezone.now(),
        closes_at=closes_at,
        metadata={"magistrate_id": mag.pk},
    )
    messages.success(request, f"Impeachment proposal for {mag.person} submitted to the assembly for vote.")
    return redirect("assembly:community_assembly", slug=community.slug)


# ---------------------------------------------------------------------------
# Election — propose via assembly
# ---------------------------------------------------------------------------

@login_required
@require_POST
def elect_propose(request, slug):
    """
    Create an assembly proposal of type magistrate_election for a given
    person + role. The community votes; when passed, elect_confirm is called.
    """
    community = get_object_or_404(Community, slug=slug)
    person = _person(request)

    if not community.members.filter(pk=person.pk).exists():
        raise PermissionDenied

    nominee_id = request.POST.get("nominee_id", "").strip()
    role_id = request.POST.get("role_id", "").strip()
    term_months = int(request.POST.get("term_months", 12) or 12)
    closes_date = request.POST.get("closes_date", "").strip()

    if not nominee_id or not role_id:
        messages.error(request, "Nominee and role are required.")
        return redirect("assembly:community_assembly", slug=slug)

    from toto.people.models import Person as PersonModel
    nominee = get_object_or_404(PersonModel, pk=nominee_id)
    role = get_object_or_404(MagistrateRole, pk=role_id)

    if not nominee.is_federal_agent:
        messages.error(request, f"{nominee} is not a federal agent and cannot hold office.")
        return redirect("assembly:community_assembly", slug=slug)

    closes_at = None
    if closes_date:
        from django.utils.dateparse import parse_datetime
        closes_at = parse_datetime(f"{closes_date}T23:59:00")

    AssemblyProposal.objects.create(
        community=community,
        proposal_type=AssemblyProposalType.MAGISTRATE_ELECTION,
        title=f"Elect {nominee} as {role} of {community}",
        body=(
            f"Proposal to elect {nominee} to the office of {role} "
            f"in {community} for a term of {term_months} months."
        ),
        status=AssemblyStatus.OPEN,
        opened_by=person,
        opens_at=timezone.now(),
        closes_at=closes_at,
        metadata={
            "nominee_id": str(nominee.pk),
            "role_id": str(role.pk),
            "term_months": term_months,
        },
    )
    messages.success(request, f"Election proposal for {nominee} as {role} submitted for assembly vote.")
    return redirect("assembly:community_assembly", slug=slug)


@login_required
@require_POST
def elect_confirm(request, proposal_pk):
    """
    Called after a magistrate_election proposal has passed.
    Creates the Magistrate record and the AssemblyDecision if not already done.
    """
    proposal = get_object_or_404(
        AssemblyProposal,
        pk=proposal_pk,
        proposal_type=AssemblyProposalType.MAGISTRATE_ELECTION,
    )
    person = _person(request)

    if proposal.status != AssemblyStatus.PASSED:
        messages.error(request, "This proposal has not passed yet.")
        return redirect("assembly:community_assembly", slug=proposal.community.slug)

    if Magistrate.objects.filter(source_proposal=proposal).exists():
        messages.info(request, "This election has already been confirmed.")
        return redirect("magistrate:overview")

    meta = proposal.metadata or {}
    from toto.people.models import Person as PersonModel
    nominee = get_object_or_404(PersonModel, pk=meta.get("nominee_id"))
    role = get_object_or_404(MagistrateRole, pk=meta.get("role_id"))
    term_months = int(meta.get("term_months", 12))

    today = timezone.now().date()
    mag = Magistrate.objects.create(
        person=nominee,
        role=role,
        community=proposal.community,
        status="active",
        term_start=today,
        term_end=today + timedelta(days=30 * term_months),
        elected_at=timezone.now(),
        source_proposal=proposal,
    )
    messages.success(request, f"{nominee} confirmed as {role} of {proposal.community}.")
    return redirect("magistrate:detail", pk=mag.pk)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@login_required
@require_POST
def report_create(request, pk):
    mag = get_object_or_404(Magistrate, pk=pk)
    person = _person(request)

    if mag.person != person:
        raise PermissionDenied

    title = request.POST.get("title", "").strip()
    body = request.POST.get("body", "").strip()

    if not title or not body:
        messages.error(request, "Title and body are required.")
        return redirect("magistrate:detail", pk=pk)

    from django.utils.dateparse import parse_date
    period_start = parse_date(request.POST.get("period_start", "") or "")
    period_end = parse_date(request.POST.get("period_end", "") or "")

    report = MagistrateReport.objects.create(
        magistrate=mag,
        title=title,
        body=body,
        reporting_period_start=period_start,
        reporting_period_end=period_end,
        status="submitted",
        submitted_at=timezone.now(),
    )
    messages.success(request, f'Report "{report.title}" submitted to the assembly.')
    return redirect("magistrate:detail", pk=pk)


@login_required
@require_POST
def report_acknowledge(request, report_pk):
    report = get_object_or_404(MagistrateReport, pk=report_pk)
    person = _person(request)

    if report.magistrate.person == person:
        messages.error(request, "You cannot acknowledge your own report.")
        return redirect("magistrate:detail", pk=report.magistrate_id)

    if report.status != "submitted":
        messages.error(request, "Only submitted reports can be acknowledged.")
        return redirect("magistrate:detail", pk=report.magistrate_id)

    report.status = "acknowledged"
    report.acknowledged_by = person
    report.acknowledged_at = timezone.now()
    report.save(update_fields=["status", "acknowledged_by", "acknowledged_at"])

    messages.success(request, f'Report "{report.title}" acknowledged.')
    return redirect("magistrate:detail", pk=report.magistrate_id)
