import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from toto.ui.page import PageProcessor

from toto.tribunal.forms import (
    JurySessionForm,
    JuryVoteForm,
    TribunalCaseForm,
    TribunalClaimForm,
    TribunalEvidenceForm,
    TribunalPartyForm,
    TribunalRulingForm,
)
from toto.tribunal.models import (
    JurySession,
    JuryVote,
    TribunalCase,
    TribunalClaim,
    TribunalEvidence,
    TribunalParty,
    TribunalRuling,
)


class TribunalChartMixin:
    chart_colors = [
        "#4f5fa1",
        "#2f3d63",
        "#5fa38c",
        "#4a8f7a",
        "#ff4455",
        "#d94a4a",
    ]

    success_color = "#5fa38c"
    accent_color = "#4f5fa1"
    accent_alt_color = "#2f3d63"
    warn_color = "#ff4455"

    def chart_json(self, chart):
        return json.dumps(chart)

    def bar_chart(self, labels, datasets, stacked=False, max_y=None):
        y_options = {"beginAtZero": True}
        if max_y is not None:
            y_options["max"] = max_y

        return {
            "chart_type": "bar",
            "labels": labels,
            "datasets": datasets,
            "options": {
                "scales": {
                    "x": {"stacked": stacked},
                    "y": {**y_options, "stacked": stacked},
                }
            },
        }

    def doughnut_chart(self, labels, data, label="Cases"):
        return {
            "chart_type": "doughnut",
            "labels": labels,
            "datasets": [{
                "label": label,
                "data": data,
                "backgroundColor": self.chart_colors,
            }],
            "options": {
                "cutout": "58%",
            },
        }


charts = TribunalChartMixin()

RESOLVING_RULING_TYPES = {
    "upheld",
    "dismissed",
    "settlement",
    "partial",
    "resolved",
    "compensation",
    "fine",
    "fine_imposed",
}


def render_tribunal(request, template_name, context):
    return render(request, template_name, PageProcessor().decorate(context, request))


def _group_counts(model, field_name):
    return list(
        model.objects
        .values(field_name)
        .annotate(total=Count("id"))
        .order_by(field_name)
    )


def _resolve_case_from_ruling(case, ruling, *, fine_created=False):
    if fine_created or ruling.resolution_type in RESOLVING_RULING_TYPES:
        case.status = "resolved"
        case.resolved_at = case.resolved_at or timezone.now()
        case.save(update_fields=["status", "resolved_at"])


def _maybe_create_fine_obligation(ruling):
    if ruling.fine_obligation_id:
        return None

    if not (
        ruling.fine_debtor_account_id
        and ruling.fine_creditor_account_id
        and ruling.fine_asset_id
        and ruling.fine_amount
    ):
        return None

    from toto.assets.services.assets import create_obligation

    due_at = ruling.fine_due_at or timezone.now() + timezone.timedelta(days=14)
    order = ruling.case.related_order
    order_reference = order.order_number if order else ruling.case.case_number
    obligation = create_obligation(
        reference=f"tribunal-fine-{ruling.pk}",
        debtor_account=ruling.fine_debtor_account,
        creditor_account=ruling.fine_creditor_account,
        asset=ruling.fine_asset,
        amount=ruling.fine_amount,
        due_at=due_at,
        order_reference=order_reference or "",
    )
    ruling.fine_due_at = due_at
    ruling.fine_obligation = obligation
    ruling.save(update_fields=["fine_due_at", "fine_obligation"])
    return obligation


def get_tribunal_dashboard_metrics():
    now = timezone.now()

    total_cases = TribunalCase.objects.count()
    open_cases = TribunalCase.objects.filter(
        status__in=["open", "under_review", "awaiting_evidence"]
    ).count()
    resolved_cases = TribunalCase.objects.exclude(resolved_at__isnull=True).count()
    urgent_cases = TribunalCase.objects.filter(priority__in=["urgent", "high"]).count()
    stale_cases = TribunalCase.objects.filter(
        resolved_at__isnull=True,
        opened_at__lt=now - timezone.timedelta(days=14),
    ).count()

    cases_with_rulings = TribunalCase.objects.filter(rulings__isnull=False).distinct().count()
    cases_with_evidence = TribunalCase.objects.filter(evidence__isnull=False).distinct().count()

    resolution_rate = round((resolved_cases / total_cases) * 100, 1) if total_cases else 0
    evidence_coverage = round((cases_with_evidence / total_cases) * 100, 1) if total_cases else 0
    ruling_coverage = round((cases_with_rulings / total_cases) * 100, 1) if total_cases else 0

    return {
        "total_cases": total_cases,
        "open_cases": open_cases,
        "resolved_cases": resolved_cases,
        "urgent_cases": urgent_cases,
        "stale_cases": stale_cases,
        "claims": TribunalClaim.objects.count(),
        "evidence": TribunalEvidence.objects.count(),
        "rulings": TribunalRuling.objects.count(),
        "parties": TribunalParty.objects.count(),
        "resolution_rate": resolution_rate,
        "evidence_coverage": evidence_coverage,
        "ruling_coverage": ruling_coverage,
        "total_claim_amount": TribunalClaim.objects.exclude(amount__isnull=True).aggregate(total=Sum("amount")).get("total"),
        "total_awarded_amount": TribunalRuling.objects.exclude(amount_awarded__isnull=True).aggregate(total=Sum("amount_awarded")).get("total"),
    }


def get_dashboard_charts():
    status_rows = _group_counts(TribunalCase, "status")
    priority_rows = _group_counts(TribunalCase, "priority")
    claim_rows = _group_counts(TribunalClaim, "claim_type")
    resolution_rows = _group_counts(TribunalRuling, "resolution_type")

    status_labels = [(row["status"] or "unspecified") for row in status_rows]
    status_data = [row["total"] for row in status_rows]

    priority_labels = [(row["priority"] or "unspecified") for row in priority_rows]
    priority_data = [row["total"] for row in priority_rows]

    claim_labels = [(row["claim_type"] or "unspecified") for row in claim_rows]
    claim_data = [row["total"] for row in claim_rows]

    resolution_labels = [(row["resolution_type"] or "unspecified") for row in resolution_rows]
    resolution_data = [row["total"] for row in resolution_rows]

    return {
        "case_status_chart_json": charts.chart_json(
            charts.doughnut_chart(status_labels, status_data, label="Cases by status")
        ),
        "case_priority_chart_json": charts.chart_json(
            charts.bar_chart(priority_labels, [{
                "label": "Cases",
                "data": priority_data,
                "backgroundColor": charts.accent_color,
            }])
        ),
        "claim_type_chart_json": charts.chart_json(
            charts.bar_chart(claim_labels, [{
                "label": "Claims",
                "data": claim_data,
                "backgroundColor": charts.warn_color,
            }])
        ),
        "resolution_type_chart_json": charts.chart_json(
            charts.doughnut_chart(resolution_labels, resolution_data, label="Rulings")
        ),
    }


def get_case_metrics(case):
    party_count = case.parties.count()
    claim_count = case.claims.count()
    evidence_count = case.evidence.count()
    ruling_count = case.rulings.count()

    total_claim_amount = case.claims.exclude(amount__isnull=True).aggregate(total=Sum("amount")).get("total")
    total_awarded_amount = case.rulings.exclude(amount_awarded__isnull=True).aggregate(total=Sum("amount_awarded")).get("total")

    age_days = None
    if case.opened_at:
        end = case.resolved_at or timezone.now()
        age_days = (end - case.opened_at).days

    evidence_per_claim = round(evidence_count / claim_count, 2) if claim_count else 0

    jury_session_count = case.jury_sessions.count()
    active_jury = case.jury_sessions.filter(status='open').first()

    readiness_score = 0
    readiness_score += 25 if party_count >= 2 else 0
    readiness_score += 25 if claim_count > 0 else 0
    readiness_score += 25 if evidence_count > 0 else 0
    readiness_score += 25 if ruling_count > 0 else 0

    return {
        "party_count": party_count,
        "claim_count": claim_count,
        "evidence_count": evidence_count,
        "ruling_count": ruling_count,
        "jury_session_count": jury_session_count,
        "active_jury": active_jury,
        "total_claim_amount": total_claim_amount,
        "total_awarded_amount": total_awarded_amount,
        "age_days": age_days,
        "evidence_per_claim": evidence_per_claim,
        "readiness_score": readiness_score,
    }


def get_case_charts(case):
    labels = ["Parties", "Claims", "Evidence", "Rulings"]
    data = [
        case.parties.count(),
        case.claims.count(),
        case.evidence.count(),
        case.rulings.count(),
    ]

    claim_rows = list(
        case.claims
        .values("claim_type")
        .annotate(total=Count("id"))
        .order_by("claim_type")
    )

    evidence_rows = list(
        case.evidence
        .values("evidence_type")
        .annotate(total=Count("id"))
        .order_by("evidence_type")
    )

    activity_chart = charts.bar_chart(labels, [{
        "label": "Case items",
        "data": data,
        "backgroundColor": charts.chart_colors,
    }])
    activity_chart["options"]["scales"]["y"]["ticks"] = {"stepSize": 1, "precision": 0}

    return {
        "case_activity_chart_json": charts.chart_json(activity_chart),
        "case_claim_type_chart_json": charts.chart_json(
            charts.doughnut_chart(
                [(row["claim_type"] or "unspecified") for row in claim_rows],
                [row["total"] for row in claim_rows],
                label="Claims",
            )
        ),
        "case_evidence_type_chart_json": charts.chart_json(
            charts.doughnut_chart(
                [(row["evidence_type"] or "unspecified") for row in evidence_rows],
                [row["total"] for row in evidence_rows],
                label="Evidence",
            )
        ),
    }


@login_required
def tribunal_dashboard(request):
    cases = (
        TribunalCase.objects
        .select_related("opened_by", "assigned_to", "related_object", "related_order")
        .annotate(
            party_count=Count("parties", distinct=True),
            claim_count=Count("claims", distinct=True),
            evidence_count=Count("evidence", distinct=True),
            ruling_count=Count("rulings", distinct=True),
        )
        .order_by("-opened_at")[:12]
    )

    return render_tribunal(request, "tribunal/dashboard.html", {
        "metrics": get_tribunal_dashboard_metrics(),
        "cases": cases,
        **get_dashboard_charts(),
    })


@login_required
def case_list(request):
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    priority = request.GET.get("priority", "").strip()

    cases = (
        TribunalCase.objects
        .select_related("opened_by", "assigned_to", "related_object", "related_order")
        .annotate(
            party_count=Count("parties", distinct=True),
            claim_count=Count("claims", distinct=True),
            evidence_count=Count("evidence", distinct=True),
            ruling_count=Count("rulings", distinct=True),
        )
    )

    if query:
        cases = cases.filter(
            Q(title__icontains=query)
            | Q(case_number__icontains=query)
            | Q(reason__icontains=query)
            | Q(description__icontains=query)
            | Q(related_object__name__icontains=query)
            | Q(related_order__order_number__icontains=query)
        )

    if status:
        cases = cases.filter(status=status)

    if priority:
        cases = cases.filter(priority=priority)

    list_metrics = {
        "result_count": cases.count(),
        "open_count": cases.filter(status__in=["open", "under_review", "awaiting_evidence"]).count(),
        "resolved_count": cases.exclude(resolved_at__isnull=True).count(),
        "urgent_count": cases.filter(priority__in=["urgent", "high"]).count(),
    }

    return render_tribunal(request, "tribunal/case_list.html", {
        "cases": cases.order_by("-opened_at"),
        "metrics": list_metrics,
        "query": query,
        "selected_status": status,
        "selected_priority": priority,
    })


@login_required
def case_detail(request, case_id):
    case = get_object_or_404(
        TribunalCase.objects.select_related(
            "opened_by",
            "assigned_to",
            "related_object",
            "related_order",
            "related_order__shop",
            "related_order__ledger_account",
            "related_order__ledger_asset",
        ).prefetch_related(
            "parties__person",
            "claims__claimant",
            "claims__currency",
            "evidence__submitted_by",
            "evidence__related_object",
            "rulings__decided_by",
            "rulings__currency",
            "rulings__fine_debtor_account",
            "rulings__fine_creditor_account",
            "rulings__fine_asset",
            "rulings__fine_obligation",
            "rulings__fine_obligation__asset",
            "jury_sessions__votes__juror",
            "jury_sessions__created_by",
        ),
        id=case_id,
    )

    jury_sessions = list(case.jury_sessions.all())
    has_jury_verdict = any(
        js.status == 'closed' and js.outcome not in ('pending', 'cancelled')
        for js in jury_sessions
    )

    return render_tribunal(request, "tribunal/case_detail.html", {
        "case": case,
        "metrics": get_case_metrics(case),
        **get_case_charts(case),
        "party_form": TribunalPartyForm(),
        "claim_form": TribunalClaimForm(case=case),
        "evidence_form": TribunalEvidenceForm(case=case),
        "ruling_form": TribunalRulingForm(case=case),
        "jury_session_form": JurySessionForm(),
        "jury_sessions": jury_sessions,
        "has_jury_verdict": has_jury_verdict,
    })


@login_required
def case_create(request):
    if request.method == "POST":
        form = TribunalCaseForm(request.POST)
        if form.is_valid():
            case = form.save()
            messages.success(request, "Tribunal case opened.")
            return redirect("tribunal:case_detail", case_id=case.id)
    else:
        form = TribunalCaseForm()

    return render_tribunal(request, "tribunal/case_form.html", {"form": form, "mode": "create"})


@require_POST
@login_required
def open_order_dispute(request, order_number):
    from toto.bazaar.models import Order

    order = get_object_or_404(
        Order.objects.select_related(
            "shop",
            "shop__ledger_account",
            "customer_profile",
            "ledger_account",
            "ledger_asset",
        ),
        order_number=order_number,
    )

    if order.user_id != request.user.id and not request.user.is_staff:
        raise PermissionDenied

    active_case = (
        TribunalCase.objects
        .filter(related_order=order, resolved_at__isnull=True)
        .exclude(status__in=["resolved", "dismissed"])
        .order_by("-opened_at")
        .first()
    )
    if active_case:
        messages.info(request, "This order already has an active tribunal dispute.")
        return redirect("tribunal:case_detail", case_id=active_case.id)

    warning_ack = (
        "Opening this dispute starts a formal tribunal process. It may affect both "
        "sides and can end in asset obligations, fines, or dismissal."
    )
    customer_note = order.customer_note.strip() if order.customer_note else ""

    with transaction.atomic():
        case = TribunalCase.objects.create(
            title=f"Dispute for order {order.order_number}",
            reason=request.POST.get("reason", "").strip() or "Bazaar order dispute",
            description=(
                f"{warning_ack}\n\n"
                f"Order: {order.order_number}\n"
                f"Shop: {order.shop.name}\n"
                f"Total: {order.total_amount} {order.currency}"
                + (f"\n\nCustomer note:\n{customer_note}" if customer_note else "")
            ),
            opened_by=order.customer_profile,
            related_order=order,
            status="open",
            priority="normal",
            metadata={
                "source": "bazaar",
                "order_number": order.order_number,
                "shop": order.shop.slug,
                "warning_acknowledged": True,
            },
        )

        claimant = None
        if order.customer_profile_id:
            claimant = TribunalParty.objects.create(
                case=case,
                person=order.customer_profile,
                role="claimant",
                statement=f"Opened from Bazaar order {order.order_number}.",
            )
        else:
            TribunalParty.objects.create(
                case=case,
                role="claimant",
                statement=f"Customer account {order.email} opened this dispute.",
            )

        TribunalParty.objects.create(
            case=case,
            role="respondent",
            statement=f"Shop respondent: {order.shop.name}.",
        )

        TribunalClaim.objects.create(
            case=case,
            claimant=claimant,
            claim_type="bazaar_order",
            summary=f"Resolve order {order.order_number}",
            description="Opened from the Bazaar order detail page.",
            requested_resolution="Review the order and issue a binding ruling if needed.",
            amount=order.total_amount,
        )

    messages.warning(
        request,
        "Dispute opened. Tribunal rulings can create consequences for both sides, including fines.",
    )
    return redirect("tribunal:case_detail", case_id=case.id)


@login_required
def case_update(request, case_id):
    case = get_object_or_404(TribunalCase, id=case_id)

    if request.method == "POST":
        form = TribunalCaseForm(request.POST, instance=case)
        if form.is_valid():
            case = form.save()
            messages.success(request, "Tribunal case updated.")
            return redirect("tribunal:case_detail", case_id=case.id)
    else:
        form = TribunalCaseForm(instance=case)

    return render_tribunal(request, "tribunal/case_form.html", {"form": form, "case": case, "mode": "update"})


@require_POST
@login_required
def case_resolve(request, case_id):
    case = get_object_or_404(TribunalCase, id=case_id)
    case.status = "resolved"
    case.resolved_at = timezone.now()
    case.save(update_fields=["status", "resolved_at"])
    messages.success(request, "Tribunal case resolved.")
    return redirect("tribunal:case_detail", case_id=case.id)


@require_POST
@login_required
def case_reopen(request, case_id):
    case = get_object_or_404(TribunalCase, id=case_id)
    case.status = "open"
    case.resolved_at = None
    case.save(update_fields=["status", "resolved_at"])
    messages.success(request, "Tribunal case reopened.")
    return redirect("tribunal:case_detail", case_id=case.id)


@require_POST
@login_required
def party_create(request, case_id):
    case = get_object_or_404(TribunalCase, id=case_id)
    form = TribunalPartyForm(request.POST)
    if form.is_valid():
        party = form.save(commit=False)
        party.case = case
        party.save()
        messages.success(request, "Party added.")
    else:
        messages.error(request, "Could not add party. Please check the form.")
    return redirect("tribunal:case_detail", case_id=case.id)


@login_required
def party_update(request, party_id):
    party = get_object_or_404(TribunalParty.objects.select_related("case", "person"), id=party_id)
    if request.method == "POST":
        form = TribunalPartyForm(request.POST, instance=party)
        if form.is_valid():
            form.save()
            messages.success(request, "Party updated.")
            return redirect("tribunal:case_detail", case_id=party.case_id)
    else:
        form = TribunalPartyForm(instance=party)
    return render_tribunal(request, "tribunal/simple_form.html", {"form": form, "case": party.case, "title": "Edit Party"})


@require_POST
@login_required
def party_delete(request, party_id):
    party = get_object_or_404(TribunalParty.objects.select_related("case"), id=party_id)
    case_id = party.case_id
    party.delete()
    messages.success(request, "Party removed.")
    return redirect("tribunal:case_detail", case_id=case_id)


@require_POST
@login_required
def claim_create(request, case_id):
    case = get_object_or_404(TribunalCase, id=case_id)
    form = TribunalClaimForm(request.POST, case=case)
    if form.is_valid():
        claim = form.save(commit=False)
        claim.case = case
        claim.save()
        messages.success(request, "Claim added.")
    else:
        messages.error(request, "Could not add claim. Please check the form.")
    return redirect("tribunal:case_detail", case_id=case.id)


@login_required
def claim_update(request, claim_id):
    claim = get_object_or_404(TribunalClaim.objects.select_related("case", "claimant", "currency"), id=claim_id)
    if request.method == "POST":
        form = TribunalClaimForm(request.POST, instance=claim, case=claim.case)
        if form.is_valid():
            form.save()
            messages.success(request, "Claim updated.")
            return redirect("tribunal:case_detail", case_id=claim.case_id)
    else:
        form = TribunalClaimForm(instance=claim, case=claim.case)
    return render_tribunal(request, "tribunal/simple_form.html", {"form": form, "case": claim.case, "title": "Edit Claim"})


@require_POST
@login_required
def claim_delete(request, claim_id):
    claim = get_object_or_404(TribunalClaim.objects.select_related("case"), id=claim_id)
    case_id = claim.case_id
    claim.delete()
    messages.success(request, "Claim removed.")
    return redirect("tribunal:case_detail", case_id=case_id)


@require_POST
@login_required
def evidence_create(request, case_id):
    case = get_object_or_404(TribunalCase, id=case_id)
    form = TribunalEvidenceForm(request.POST, request.FILES, case=case)
    if form.is_valid():
        evidence = form.save(commit=False)
        evidence.case = case
        evidence.save()
        messages.success(request, "Evidence submitted.")
    else:
        messages.error(request, "Could not submit evidence. Please check the form.")
    return redirect("tribunal:case_detail", case_id=case.id)


@login_required
def evidence_update(request, evidence_id):
    evidence = get_object_or_404(TribunalEvidence.objects.select_related("case", "submitted_by", "related_object"), id=evidence_id)
    if request.method == "POST":
        form = TribunalEvidenceForm(request.POST, request.FILES, instance=evidence, case=evidence.case)
        if form.is_valid():
            form.save()
            messages.success(request, "Evidence updated.")
            return redirect("tribunal:case_detail", case_id=evidence.case_id)
    else:
        form = TribunalEvidenceForm(instance=evidence, case=evidence.case)
    return render_tribunal(request, "tribunal/simple_form.html", {"form": form, "case": evidence.case, "title": "Edit Evidence"})


@require_POST
@login_required
def evidence_delete(request, evidence_id):
    evidence = get_object_or_404(TribunalEvidence.objects.select_related("case"), id=evidence_id)
    case_id = evidence.case_id
    evidence.delete()
    messages.success(request, "Evidence removed.")
    return redirect("tribunal:case_detail", case_id=case_id)


@require_POST
@login_required
def ruling_create(request, case_id):
    case = get_object_or_404(TribunalCase.objects.select_related("related_order"), id=case_id)
    form = TribunalRulingForm(request.POST, case=case)
    if form.is_valid():
        try:
            with transaction.atomic():
                ruling = form.save(commit=False)
                ruling.case = case
                ruling.save()
                fine_obligation = _maybe_create_fine_obligation(ruling)
                _resolve_case_from_ruling(case, ruling, fine_created=bool(fine_obligation))
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if hasattr(exc, "messages") else str(exc))
        else:
            if fine_obligation:
                messages.success(
                    request,
                    f"Ruling recorded and fine obligation {fine_obligation.reference} created.",
                )
            else:
                messages.success(request, "Ruling recorded.")
    else:
        messages.error(request, "Could not record ruling. Please check the form.")
    return redirect("tribunal:case_detail", case_id=case.id)


@login_required
def ruling_update(request, ruling_id):
    ruling = get_object_or_404(
        TribunalRuling.objects.select_related(
            "case",
            "case__related_order",
            "decided_by",
            "currency",
            "fine_obligation",
        ),
        id=ruling_id,
    )
    if request.method == "POST":
        form = TribunalRulingForm(request.POST, instance=ruling, case=ruling.case)
        if form.is_valid():
            try:
                with transaction.atomic():
                    ruling = form.save()
                    fine_obligation = _maybe_create_fine_obligation(ruling)
                    _resolve_case_from_ruling(ruling.case, ruling, fine_created=bool(fine_obligation))
            except ValidationError as exc:
                messages.error(request, exc.messages[0] if hasattr(exc, "messages") else str(exc))
                return redirect("tribunal:ruling_update", ruling_id=ruling.id)
            messages.success(
                request,
                "Ruling updated and fine obligation created." if fine_obligation else "Ruling updated.",
            )
            return redirect("tribunal:case_detail", case_id=ruling.case_id)
    else:
        form = TribunalRulingForm(instance=ruling, case=ruling.case)
    return render_tribunal(request, "tribunal/simple_form.html", {"form": form, "case": ruling.case, "title": "Edit Ruling"})


@require_POST
@login_required
def ruling_delete(request, ruling_id):
    ruling = get_object_or_404(TribunalRuling.objects.select_related("case"), id=ruling_id)
    case_id = ruling.case_id
    ruling.delete()
    messages.success(request, "Ruling removed.")
    return redirect("tribunal:case_detail", case_id=case_id)


@require_POST
@login_required
def jury_session_open(request, case_id):
    if not request.user.is_staff:
        raise PermissionDenied
    case = get_object_or_404(TribunalCase, id=case_id)
    form = JurySessionForm(request.POST)
    if form.is_valid():
        session = form.save(commit=False)
        session.case = case
        session.opens_at = timezone.now()
        session.status = 'open'
        try:
            session.created_by = request.user.community_profile
        except Exception:
            pass
        session.save()
        messages.success(request, "Jury session opened.")
    else:
        messages.error(request, "Could not open jury session: " + str(form.errors))
    return redirect("tribunal:case_detail", case_id=case_id)


def _jury_session_ctx(session, user):
    tally = session.tally()
    total = sum(tally.values())
    chart_data = json.dumps({
        "chart_type": "doughnut",
        "labels": ["Guilty", "Not Guilty", "Abstain"],
        "datasets": [{
            "label": "Votes",
            "data": [tally["guilty"], tally["not_guilty"], tally["abstain"]],
            "backgroundColor": ["#ff4455", "#5fa38c", "#4f5fa1"],
        }],
        "options": {"cutout": "55%"},
    })
    user_person = getattr(user, 'community_profile', None)
    user_vote = session.votes.filter(juror=user_person).first() if user_person else None
    invited = list(session.jurors.all())
    is_invited = (not invited) or (user_person and any(j.pk == user_person.pk for j in invited))
    votes_by_juror = {v.juror_id: v for v in session.votes.all()}
    juror_rows = [(juror, votes_by_juror.get(juror.pk)) for juror in invited]
    vote_form = JuryVoteForm() if session.is_open and not user_vote and is_invited else None
    return {
        "session": session,
        "tally": tally,
        "total": total,
        "chart_data": chart_data,
        "vote_form": vote_form,
        "user_vote": user_vote,
        "user_person": user_person,
        "juror_rows": juror_rows,
        "is_invited": is_invited,
    }


@login_required
def jury_session_detail(request, session_id):
    session = get_object_or_404(
        JurySession.objects.select_related("case", "created_by").prefetch_related("votes__juror", "jurors"),
        id=session_id,
    )
    return render_tribunal(request, "tribunal/jury_session_detail.html", _jury_session_ctx(session, request.user))


@require_POST
@login_required
def jury_vote(request, session_id):
    session = get_object_or_404(JurySession, id=session_id)
    is_htmx = bool(request.headers.get('HX-Request'))
    detail_url = reverse('tribunal:jury_session_detail', kwargs={'session_id': session_id})

    def _htmx_redirect():
        r = HttpResponse(status=204)
        r['HX-Redirect'] = detail_url
        return r

    if not session.is_open:
        if is_htmx:
            return _htmx_redirect()
        messages.error(request, "This jury session is no longer open for voting.")
        return redirect(detail_url)

    user_person = getattr(request.user, 'community_profile', None)
    if not user_person:
        if is_htmx:
            return _htmx_redirect()
        messages.error(request, "You need a person profile to cast a vote.")
        return redirect(detail_url)

    invited = session.jurors.all()
    if invited.exists() and not invited.filter(pk=user_person.pk).exists():
        if is_htmx:
            return _htmx_redirect()
        messages.error(request, "You are not invited to vote in this session.")
        return redirect(detail_url)

    if session.votes.filter(juror=user_person).exists():
        if is_htmx:
            return _htmx_redirect()
        messages.error(request, "You have already voted in this session.")
        return redirect(detail_url)

    form = JuryVoteForm(request.POST)
    if not form.is_valid():
        if is_htmx:
            return _htmx_redirect()
        messages.error(request, "Invalid vote submission.")
        return redirect(detail_url)

    vote = form.save(commit=False)
    vote.session = session
    vote.juror = user_person
    vote.save()

    if is_htmx:
        session = get_object_or_404(
            JurySession.objects.select_related("case", "created_by").prefetch_related("votes__juror", "jurors"),
            id=session_id,
        )
        return render_tribunal(request, "tribunal/_jury_live.html", _jury_session_ctx(session, request.user))

    messages.success(request, f"Vote recorded: {vote.get_vote_display()}.")
    return redirect(detail_url)


@require_POST
@login_required
def jury_session_close(request, session_id):
    if not request.user.is_staff:
        raise PermissionDenied
    session = get_object_or_404(JurySession.objects.select_related("case"), id=session_id)
    outcome = session.compute_outcome()
    session.status = 'closed'
    session.outcome = outcome
    session.save(update_fields=['status', 'outcome'])
    messages.success(request, f"Jury session closed. Outcome: {outcome.replace('_', ' ').title()}.")
    return redirect("tribunal:jury_session_detail", session_id=session_id)
