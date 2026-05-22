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

from .models import Magistrate, MagistrateReport, MagistrateRole
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

    return _render(request, "magistrate/magistrate_detail.html", {
        "mag": mag,
        "reports": reports,
        "person": person,
        "can_report": mag.person == person,
        "can_acknowledge": person != mag.person,
    })


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
