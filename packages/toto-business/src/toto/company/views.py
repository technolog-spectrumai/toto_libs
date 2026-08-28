"""Three views the brief names, plus the department pages behind them.

`Company Structure`, `Shareholders` and `Organization Chart` are separate pages
rather than tabs on one detail screen — irena had a single 515-line
`company_detail.html`, and splitting it is the one deliberate departure from
that code, because the brief asks for it by name.

The ownership pie and the organization graph are irena's own computations,
moved here unchanged apart from URL names.
"""

from __future__ import annotations

import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from toto.company.forms import (
    CompanyActionForm,
    CompanyDetailsForm,
    CompanyMembershipForm,
    DepartmentForm,
    DepartmentMembershipForm,
    ShareClassForm,
    ShareholderStructureForm,
)
from toto.company.integration import ledger as bc_ledger
from toto.company.models import (
    ActionStatus,
    Company,
    CompanyAction,
    CompanyMembership,
    Department,
    ShareHolding,
)
from toto.ui import PageProcessor


#: Chart.js slice colours. The house chart partial reads `theme.theme.colors`
#: for text and grid, but a pie needs its own series palette.
PALETTE = ["#4F46E5", "#10B981", "#F59E0B", "#EF4444", "#3B82F6", "#8B5CF6"]


def company_render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _staff_only(user):
    if not user.is_staff:
        raise PermissionDenied("Only staff may change company facts.")


def _company(slug):
    return get_object_or_404(Company, slug=slug)


def _tabs(company, active):
    """The three views the brief names, as one strip."""
    return [
        {"key": "structure", "label": "Company Structure", "icon": "fa-building",
         "url": reverse("company:structure", args=[company.slug]),
         "active": active == "structure"},
        {"key": "shareholders", "label": "Shareholders", "icon": "fa-chart-pie",
         "url": reverse("company:shareholders", args=[company.slug]),
         "active": active == "shareholders"},
        {"key": "chart", "label": "Organization Chart", "icon": "fa-sitemap",
         "url": reverse("company:org_chart", args=[company.slug]),
         "active": active == "chart"},
        {"key": "actions", "label": "Actions", "icon": "fa-link",
         "url": reverse("company:actions", args=[company.slug]),
         "active": active == "actions"},
        {"key": "votes", "label": "Votes", "icon": "fa-check-to-slot",
         "url": reverse("company:votes", args=[company.slug]),
         "active": active == "votes"},
    ]


def _department(company_slug, slug):
    return get_object_or_404(
        Department.objects.select_related("company", "parent", "head"),
        company__slug=company_slug,
        slug=slug,
    )


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


@login_required
def index(request):
    companies = list(
        Company.objects.filter(active=True)
        .prefetch_related("departments", "memberships")
        .order_by("name")
    )
    return company_render(request, "company/index.html", {
        "companies": companies,
        "company_count": Company.objects.count(),
    })


# ---------------------------------------------------------------------------
# 1. Company Structure
# ---------------------------------------------------------------------------


@login_required
def structure(request, slug):
    company = _company(slug)
    company_form = CompanyDetailsForm(instance=company)
    department_form = DepartmentForm(company=company)
    membership_form = CompanyMembershipForm(company=company)
    open_modal = ""

    if request.method == "POST":
        _staff_only(request.user)
        action = request.POST.get("action")
        if action == "company":
            company_form = CompanyDetailsForm(request.POST, request.FILES, instance=company)
            if company_form.is_valid():
                company_form.save()
                messages.success(request, "Company facts saved.")
                return redirect("company:structure", slug=company.slug)
            open_modal = "company"
        elif action == "department":
            department_form = DepartmentForm(request.POST, company=company)
            if department_form.is_valid():
                try:
                    department_form.save()
                except ValidationError as exc:
                    department_form.add_error(None, exc)
                    open_modal = "department"
                else:
                    messages.success(request, "Department added.")
                    return redirect("company:structure", slug=company.slug)
            else:
                open_modal = "department"
        elif action == "membership":
            membership_form = CompanyMembershipForm(request.POST, company=company)
            if membership_form.is_valid():
                membership_form.save()
                messages.success(request, "Member added.")
                return redirect("company:structure", slug=company.slug)
            open_modal = "membership"
        else:
            messages.error(request, "Unknown form action.")

    departments = list(
        company.departments.filter(active=True)
        .select_related("parent", "head")
        .prefetch_related("memberships__party")
        .order_by("name")
    )
    return company_render(request, "company/structure.html", {
        "company": company,
        "tabs": _tabs(company, "structure"),
        "company_form": company_form,
        "department_form": department_form,
        "membership_form": membership_form,
        "open_modal": open_modal,
        "departments": _flatten_departments(departments),
        "memberships": company.memberships.filter(active=True).select_related(
            "person", "primary_department",
        ).order_by("person__display_name"),
        "parties": company.parties.select_related("person").order_by("name"),
        "org_chart_url": reverse("company:org_chart", args=[company.slug]),
    })


def _flatten_departments(departments):
    """Depth-annotated, parents before children, for an indented table."""
    by_parent = {}
    for department in departments:
        by_parent.setdefault(department.parent_id, []).append(department)
    known = {department.pk for department in departments}
    rows = []

    def walk(parent_id, depth):
        for department in by_parent.get(parent_id, []):
            department.depth = depth
            rows.append(department)
            walk(department.pk, depth + 1)

    walk(None, 0)
    # A department whose parent is filtered out (inactive) would otherwise
    # vanish from the table entirely. Show it at the root instead.
    for department in departments:
        if department.parent_id is not None and department.parent_id not in known:
            department.depth = 0
            rows.append(department)
            walk(department.pk, 1)
    return rows


# ---------------------------------------------------------------------------
# 2. Shareholders
# ---------------------------------------------------------------------------


@login_required
def shareholders(request, slug):
    company = _company(slug)
    share_class_form = ShareClassForm(company=company)
    shareholder_form = ShareholderStructureForm(company=company, recorded_by=request.user)
    open_modal = ""

    if request.method == "POST":
        _staff_only(request.user)
        action = request.POST.get("action")
        if action == "share_class":
            share_class_form = ShareClassForm(request.POST, company=company)
            if share_class_form.is_valid():
                share_class_form.save()
                messages.success(request, "Share class added.")
                return redirect("company:shareholders", slug=company.slug)
            open_modal = "share_class"
        elif action == "shareholder":
            shareholder_form = ShareholderStructureForm(
                request.POST, company=company, recorded_by=request.user,
            )
            if shareholder_form.is_valid():
                shareholder_form.save()
                messages.success(request, "Shareholder structure recorded.")
                return redirect("company:shareholders", slug=company.slug)
            open_modal = "shareholder"
        else:
            messages.error(request, "Unknown form action.")

    register = ownership_register(company)
    return company_render(request, "company/shareholders.html", {
        "company": company,
        "tabs": _tabs(company, "shareholders"),
        "share_class_form": share_class_form,
        "shareholder_form": shareholder_form,
        "open_modal": open_modal,
        "share_classes": company.share_classes.order_by("slug"),
        **register,
        "ownership_events": company.ownership_events.select_related(
            "source_party", "target_party", "source_share_class",
            "target_share_class", "recorded_by",
        ).order_by("-effective_on", "-recorded_at")[:30],
    })


def ownership_register(company):
    """The live register, exact to the last unit, plus its pie.

    Percentages are display only and rounded; `units` and `votes` are the
    Decimals the register is judged on and are never rounded here.
    """
    holdings = list(
        ShareHolding.objects
        .filter(share_class__company=company, until__isnull=True)
        .select_related("party", "share_class")
        .order_by("share_class__slug", "party__name")
    )
    total_units = sum((holding.units for holding in holdings), 0)
    total_votes = sum((holding.votes for holding in holdings), 0)

    rows = []
    by_shareholder = {}
    for holding in holdings:
        by_shareholder[holding.party.name] = (
            by_shareholder.get(holding.party.name, 0) + holding.votes
        )
        rows.append({
            "holding": holding,
            "name": holding.party.name,
            "units": holding.units,
            "votes": holding.votes,
            "percent": round(holding.votes / total_votes * 100, 1) if total_votes else 0,
        })

    chart_rows = [
        {"name": name, "votes": votes}
        for name, votes in sorted(by_shareholder.items())
        if votes
    ]
    chart_json = json.dumps({
        "chart_type": "pie",
        "labels": [row["name"] for row in chart_rows],
        "datasets": [{
            "data": [float(row["votes"]) for row in chart_rows],
            "backgroundColor": PALETTE,
        }],
    }) if chart_rows else ""

    return {
        "holdings": holdings,
        "shareholder_structure": rows,
        "shareholder_chart_json": chart_json,
        "total_units": total_units,
        "total_votes": total_votes,
    }


# ---------------------------------------------------------------------------
# 3. Organization Chart
# ---------------------------------------------------------------------------


@login_required
def org_chart(request, slug):
    company = _company(slug)
    return company_render(request, "company/org_chart.html", {
        "company": company,
        "tabs": _tabs(company, "chart"),
        "graph_url": reverse("company:company_graph", args=[company.slug]),
        "departments": _flatten_departments(list(
            company.departments.filter(active=True)
            .select_related("parent", "head")
            .order_by("name")
        )),
    })


def _party_url(party):
    if not getattr(settings, "BUILD_SOCIALHUB", False) or not party.person_id:
        return ""
    return reverse("socialhub:profile_details", args=[party.person.slug])


def organization_graph(company, *, root=None, include_members=False):
    departments = list(
        company.departments.filter(active=True).select_related(
            "parent", "head__person",
        ).prefetch_related("memberships__party__person")
    )
    if root is not None:
        included = {root.pk}
        changed = True
        while changed:
            changed = False
            for department in departments:
                if department.parent_id in included and department.pk not in included:
                    included.add(department.pk)
                    changed = True
        departments = [department for department in departments if department.pk in included]

    nodes = []
    edges = []
    included_ids = {department.pk for department in departments}
    if root is None:
        nodes.append({
            "id": f"company-{company.pk}",
            "label": company.name,
            "type": "company",
            "url": reverse("company:structure", args=[company.slug]),
        })
    person_nodes = set()
    for department in departments:
        department_id = f"department-{department.pk}"
        nodes.append({
            "id": department_id,
            "label": department.name,
            "type": "department",
            "url": reverse("company:department_detail", args=[company.slug, department.slug]),
        })
        if department.parent_id in included_ids:
            parent_id = f"department-{department.parent_id}"
        elif root is None:
            parent_id = f"company-{company.pk}"
        else:
            parent_id = ""
        if parent_id:
            edges.append({
                "source": parent_id,
                "target": department_id,
                "type": "parent_child",
            })
        if department.head_id:
            person_id = f"party-{department.head_id}"
            if person_id not in person_nodes:
                person_nodes.add(person_id)
                nodes.append({
                    "id": person_id,
                    "label": department.head.name,
                    "type": "person",
                    "url": _party_url(department.head),
                })
            edges.append({
                "source": department_id,
                "target": person_id,
                "type": "head",
                "label": "head",
            })
        if include_members:
            for membership in department.memberships.all():
                if not membership.active or membership.party_id == department.head_id:
                    continue
                person_id = f"party-{membership.party_id}"
                if person_id not in person_nodes:
                    person_nodes.add(person_id)
                    nodes.append({
                        "id": person_id,
                        "label": membership.party.name,
                        "type": "person",
                        "url": _party_url(membership.party),
                    })
                edges.append({
                    "source": department_id,
                    "target": person_id,
                    "type": "member",
                    "label": membership.title,
                })
    return {"nodes": nodes, "edges": edges}


@login_required
def company_graph(request, slug):
    company = _company(slug)
    return JsonResponse(organization_graph(
        company,
        include_members=request.GET.get("members") == "1",
    ))


@login_required
def department_graph(request, company_slug, slug):
    department = _department(company_slug, slug)
    return JsonResponse(organization_graph(
        department.company,
        root=department,
        include_members=True,
    ))


# ---------------------------------------------------------------------------
# Department pages
# ---------------------------------------------------------------------------


@login_required
def department_detail(request, company_slug, slug):
    department = _department(company_slug, slug)
    return company_render(request, "company/department_detail.html", {
        "company": department.company,
        "tabs": _tabs(department.company, "chart"),
        "department": department,
        "ancestors": department.ancestors(),
        "children": department.children.filter(active=True).order_by("name"),
        "memberships": department.memberships.filter(active=True).select_related(
            "party__person",
        ),
        "membership_form": DepartmentMembershipForm(department=department),
        "graph_url": reverse(
            "company:department_graph", args=[department.company.slug, department.slug],
        ),
    })


@login_required
@require_POST
def department_edit(request, company_slug, slug):
    department = _department(company_slug, slug)
    _staff_only(request.user)
    form = DepartmentForm(request.POST, company=department.company, instance=department)
    if form.is_valid():
        try:
            form.save()
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, "Department saved.")
    else:
        messages.error(request, "The department could not be saved.")
    return redirect("company:department_detail", company_slug=company_slug, slug=department.slug)


@login_required
@require_POST
def membership_add(request, company_slug, slug):
    department = _department(company_slug, slug)
    _staff_only(request.user)
    form = DepartmentMembershipForm(request.POST, department=department)
    if form.is_valid():
        try:
            form.save()
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, "Member added to the department.")
    else:
        messages.error(request, "The member could not be added.")
    return redirect("company:department_detail", company_slug=company_slug, slug=slug)


# ---------------------------------------------------------------------------
# 4. Actions — drafts, and the deliberate act of recording one
# ---------------------------------------------------------------------------


@login_required
def actions(request, slug):
    company = _company(slug)
    form = CompanyActionForm(company=company)

    if request.method == "POST":
        _staff_only(request.user)
        form = CompanyActionForm(request.POST, company=company)
        if form.is_valid():
            action = form.save(commit=False)
            action.created_by = request.user
            action.save()
            messages.success(request, "Draft saved. Record it when it is final.")
            return redirect("company:actions", slug=company.slug)

    ledger = bc_ledger.existing_ledger(company)
    return company_render(request, "company/actions.html", {
        "company": company,
        "tabs": _tabs(company, "actions"),
        "form": form,
        "drafts": company.actions.filter(status=ActionStatus.DRAFT)
                                 .select_related("created_by"),
        "recorded": company.actions.filter(status=ActionStatus.RECORDED)
                                   .select_related("created_by")[:50],
        "ledger": ledger,
        "verification": bc_ledger.verify_company_ledger(company),
    })


@login_required
@require_POST
def action_record(request, slug, uid):
    """Freeze the draft and append it. Both, or neither.

    Deliberately a POST with no confirmation dialogue in the way: the button
    itself says Record, the page says what recording means, and a modal that
    asks "are you sure" would only train people to click through it.
    """
    company = _company(slug)
    _staff_only(request.user)
    action = get_object_or_404(CompanyAction, company=company, uid=uid)
    try:
        entry = bc_ledger.record_action(action, actor=request.user)
    except (ValueError, ValidationError) as exc:
        messages.error(request, str(exc))
        return redirect("company:actions", slug=company.slug)
    messages.success(
        request, f"Recorded as block {entry.sequence}. It cannot be changed now.",
    )
    return redirect("ledger:block", uid=entry.ledger.uid, sequence=entry.sequence)


@login_required
@require_POST
def action_delete(request, slug, uid):
    company = _company(slug)
    _staff_only(request.user)
    action = get_object_or_404(CompanyAction, company=company, uid=uid)
    try:
        action.delete()
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "Draft deleted.")
    return redirect("company:actions", slug=company.slug)


# ---------------------------------------------------------------------------
# 5. Votes — recorded, attributable, never secret
# ---------------------------------------------------------------------------


@login_required
def votes(request, slug):
    company = _company(slug)
    from toto.company.integration import voting as bc_voting

    meetings = list(
        bc_voting.meetings_for(company)
        .prefetch_related("propositions", "roll")
        .order_by("-held_at", "-created_at")
    )
    return company_render(request, "company/votes.html", {
        "company": company,
        "tabs": _tabs(company, "votes"),
        "meetings": meetings,
        "is_member": bc_voting.is_member(company, request.user),
    })


@login_required
def vote_detail(request, slug, uid):
    """Propositions, participation, totals, outcome, and who voted how."""
    from toto.company.integration import voting as bc_voting
    from toto.voting.models import AttendanceStatus, Meeting
    from toto.voting.services.tally import tally

    company = _company(slug)
    meeting = get_object_or_404(
        Meeting, uid=uid, scope_type=bc_voting.SCOPE_TYPE, scope_uid=company.uid,
    )

    my_ref = bc_voting.user_ref(request.user) if request.user.is_authenticated else ""
    propositions = []
    for proposition in meeting.propositions.order_by("order"):
        # A finalized proposition shows its FROZEN result, not a fresh count —
        # the frozen one is what was decided and what went on the chain.
        if proposition.is_final and proposition.result_payload:
            result = proposition.result_payload.get("result", {})
        else:
            result = tally(proposition)
        my_entry = meeting.roll.filter(
            models_q_ref(my_ref)
        ).first() if my_ref else None
        propositions.append({
            "proposition": proposition,
            "result": result,
            "ballots": proposition.ballots.select_related("roll_entry")
                                          .order_by("cast_at"),
            "my_entry": my_entry,
            "my_ballot": (proposition.ballots.filter(
                roll_entry=my_entry, active=True).first() if my_entry else None),
        })

    return company_render(request, "company/vote_detail.html", {
        "company": company,
        "tabs": _tabs(company, "votes"),
        "meeting": meeting,
        "propositions": propositions,
        "roll": meeting.roll.order_by("voter_name"),
        "present_status": AttendanceStatus.PRESENT,
        "is_member": bc_voting.is_member(company, request.user),
        "chain_block_url": "ledger:block",
    })


def models_q_ref(ref):
    from django.db.models import Q

    return Q(voter_ref=ref) | Q(represented_by=ref)


@login_required
@require_POST
def vote_open(request, slug, uid):
    from toto.company.integration import voting as bc_voting
    from toto.voting.models import Meeting
    from toto.voting.services import lifecycle as vote_lifecycle

    company = _company(slug)
    meeting = get_object_or_404(Meeting, uid=uid, scope_type=bc_voting.SCOPE_TYPE,
                                scope_uid=company.uid)
    try:
        bc_voting.open_company_meeting(meeting, opened_by=request.user)
        for proposition in meeting.propositions.all():
            vote_lifecycle.open_proposition(proposition, opened_by=request.user)
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _first_message(exc))
    else:
        messages.success(request, "Voting is open. The electorate is now frozen.")
    return redirect("company:vote_detail", slug=company.slug, uid=meeting.uid)


@login_required
@require_POST
def vote_cast(request, slug, uid, proposition_uid):
    """Cast or change one vote. Requires a confirmation made just now."""
    from toto.company.integration import voting as bc_voting
    from toto.voting.models import Meeting, Proposition

    company = _company(slug)
    meeting = get_object_or_404(Meeting, uid=uid, scope_type=bc_voting.SCOPE_TYPE,
                                scope_uid=company.uid)
    proposition = get_object_or_404(Proposition, uid=proposition_uid, meeting=meeting)

    if request.POST.get("confirm") != "yes":
        messages.error(request, "Tick the confirmation before casting.")
        return redirect("company:vote_detail", slug=company.slug, uid=meeting.uid)

    my_ref = bc_voting.user_ref(request.user)
    entry = meeting.roll.filter(models_q_ref(my_ref)).first()
    if entry is None:
        messages.error(request, "You are not on this meeting's roll.")
        return redirect("company:vote_detail", slug=company.slug, uid=meeting.uid)

    try:
        bc_voting.cast(
            proposition=proposition, entry=entry,
            choice=request.POST.get("choice", ""), cast_by=request.user,
            # The confirmation happened in this request: the tick above IS the
            # fresh act. Recording `now()` is honest here precisely because the
            # POST that carries it is the one being confirmed.
            confirmed_at=timezone.now(),
            auth_evidence={
                "method": "session+confirmation",
                "user": request.user.get_username(),
                "ip": request.META.get("REMOTE_ADDR", ""),
                "user_agent": request.META.get("HTTP_USER_AGENT", "")[:200],
            },
        )
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _first_message(exc))
    else:
        messages.success(request, "Your vote is recorded against your name.")
    return redirect("company:vote_detail", slug=company.slug, uid=meeting.uid)


@login_required
@require_POST
def vote_finalize(request, slug, uid, proposition_uid):
    from toto.company.integration import voting as bc_voting
    from toto.voting.models import Meeting, Proposition

    company = _company(slug)
    meeting = get_object_or_404(Meeting, uid=uid, scope_type=bc_voting.SCOPE_TYPE,
                                scope_uid=company.uid)
    proposition = get_object_or_404(Proposition, uid=proposition_uid, meeting=meeting)
    try:
        bc_voting.finalize_proposition(proposition, decided_by=request.user)
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, _first_message(exc))
    else:
        proposition.refresh_from_db()
        messages.success(
            request,
            "Finalized and written to the chain. It cannot be changed now.",
        )
    return redirect("company:vote_detail", slug=company.slug, uid=meeting.uid)


def _first_message(exc):
    messages_list = getattr(exc, "messages", None)
    if messages_list:
        return "; ".join(messages_list)
    return str(exc)


@login_required
@require_POST
def vote_export(request, slug, uid, proposition_uid=None):
    """Export the meeting record, or one decision, as a PDF through aralia."""
    from toto.company.integration import voting as bc_voting
    from toto.documents import builders, services
    from toto.voting.models import Meeting, Proposition

    company = _company(slug)
    meeting = get_object_or_404(Meeting, uid=uid, scope_type=bc_voting.SCOPE_TYPE,
                                scope_uid=company.uid)
    if proposition_uid:
        proposition = get_object_or_404(Proposition, uid=proposition_uid,
                                        meeting=meeting)
        html = builders.vote_document(proposition)
        label = f"decision {proposition.title}"
    else:
        html = builders.meeting_document(meeting)
        label = f"meeting {meeting.title}"

    try:
        services.export(html, user=request.user, label=label)
    except services.ExportRefused as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "The PDF is rendering.")
    return redirect("company:vote_detail", slug=company.slug, uid=meeting.uid)
