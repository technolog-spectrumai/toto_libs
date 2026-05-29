from __future__ import annotations

from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.generic import TemplateView

from toto.ui import PageProcessor
from toto.contracts.models import Contract, ContractNode


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _get_person_for_request(request):
    if not request.user.is_authenticated:
        return None
    try:
        return request.user.community_profile
    except Exception:
        return None


def _loan_ctx(contract):
    def _obj(key):
        n = contract.nodes.filter(key=key).first()
        return n.get_object() if n else None

    meta = contract.metadata or {}
    interest_bps = meta.get("interest_rate_bps", 0)

    return {
        "contract": contract,
        "lender_person": _obj("lender_person"),
        "borrower_person": _obj("borrower_person"),
        "lender_account": _obj("lender_acct"),
        "borrower_account": _obj("borrower_acct"),
        "loan_asset": _obj("loan_asset"),
        "principal_base_units": meta.get("principal_base_units"),
        "interest_rate_bps": interest_bps,
        "interest_rate_pct": interest_bps / 100 if interest_bps else 0,
        "repayment_frequency": meta.get("repayment_frequency", "—"),
        "term_months": meta.get("term_months"),
    }


# ---------------------------------------------------------------------------
# Loan — list
# ---------------------------------------------------------------------------

def loan_list(request):
    qs = (
        Contract.objects
        .filter(metadata__archetype="loan")
        .prefetch_related("nodes")
        .order_by("name")
    )
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(name__icontains=q)

    rows = []
    for c in qs:
        def _obj(key, _c=c):
            n = _c.nodes.filter(key=key).first()
            return n.get_object() if n else None

        meta = c.metadata or {}
        rows.append({
            "contract": c,
            "lender": _obj("lender_person"),
            "borrower": _obj("borrower_person"),
            "loan_asset": _obj("loan_asset"),
            "principal_base_units": meta.get("principal_base_units"),
            "interest_rate_bps": meta.get("interest_rate_bps", 0),
            "term_months": meta.get("term_months"),
            "repayment_frequency": meta.get("repayment_frequency", "—"),
        })

    return _render(request, "contracts/loan_list.html", {"rows": rows, "q": q})


# ---------------------------------------------------------------------------
# Loan — create
# ---------------------------------------------------------------------------

def loan_create(request):
    from toto.contracts.forms import LoanCreateForm
    if request.method == "POST":
        form = LoanCreateForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            from toto.loans.services import create_loan_contract
            contract = create_loan_contract(
                name=cd["name"],
                lender_person=cd["lender_person"],
                borrower_person=cd["borrower_person"],
                lender_account=cd["lender_account"],
                borrower_account=cd["borrower_account"],
                loan_asset=cd["loan_asset"],
                principal_base_units=cd["principal_base_units"],
                interest_rate_bps=cd["interest_rate_bps"],
                repayment_frequency=cd["repayment_frequency"],
                term_months=cd["term_months"],
            )
            messages.success(request, f"Loan contract '{contract.name}' created.")
            return redirect("loans:detail", uuid=contract.uuid)
    else:
        form = LoanCreateForm()
    return _render(request, "contracts/loan_form.html", {"form": form})


# ---------------------------------------------------------------------------
# Loan — detail
# ---------------------------------------------------------------------------

def loan_detail(request, uuid):
    contract = get_object_or_404(Contract, uuid=uuid, metadata__archetype="loan")
    ctx = _loan_ctx(contract)

    signatories = list(contract.signatories.select_related("person").all())
    current_person = _get_person_for_request(request)
    ctx["signatories"] = signatories
    ctx["current_person_signatory"] = next(
        (s for s in signatories if current_person and s.person_id == current_person.pk), None
    )

    return _render(request, "contracts/loan_detail.html", ctx)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class LoanMetricsView(LoginRequiredMixin, TemplateView):
    template_name = "loans/metrics.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        from toto.assets.models import Obligation, ObligationStatus

        qs = Contract.objects.filter(metadata__archetype="loan")
        total_contracts = qs.count()
        draft_contracts = qs.filter(status=Contract.STATUS_DRAFT).count()
        executed_contracts = qs.filter(status=Contract.STATUS_EXECUTED).count()

        week_ago = timezone.now() - timedelta(days=7)
        recent_contracts = qs.filter(created_at__gte=week_ago).count()

        contract_pks = list(qs.values_list("pk", flat=True))

        disb_ids = list(
            ContractNode.objects.filter(
                contract__in=contract_pks, node_type="obligation",
                key__regex=r"^disbursement_\d+$",
            ).values_list("object_id", flat=True)
        )
        rep_ids = list(
            ContractNode.objects.filter(
                contract__in=contract_pks, node_type="obligation",
                key__regex=r"^repayment_\d+$",
            ).values_list("object_id", flat=True)
        )

        disb_qs = Obligation.objects.filter(pk__in=disb_ids)
        rep_qs = Obligation.objects.filter(pk__in=rep_ids)

        total_disbursements = disb_qs.count()
        total_repayments = rep_qs.count()
        pending_repayments = rep_qs.filter(status=ObligationStatus.PENDING).count()
        settled_repayments = rep_qs.filter(status=ObligationStatus.FULFILLED).count()

        context.update({
            "total_contracts": total_contracts,
            "draft_contracts": draft_contracts,
            "executed_contracts": executed_contracts,
            "recent_contracts": recent_contracts,
            "total_disbursements": total_disbursements,
            "pending_repayments": pending_repayments,
            "settled_repayments": settled_repayments,
        })

        context["status_series"] = list(
            qs.values("status").annotate(count=Count("id")).order_by("status")
        )
        context["repayment_status_series"] = list(
            rep_qs.values("status").annotate(count=Count("id")).order_by("status")
        )
        context["type_series"] = [
            {"label": "Disbursements", "count": total_disbursements},
            {"label": "Repayments", "count": total_repayments},
        ]

        thirty_days_ago = timezone.now() - timedelta(days=29)
        daily_map = {
            entry["day"]: entry["count"]
            for entry in qs.filter(created_at__gte=thirty_days_ago)
            .annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(count=Count("id"))
        }
        today = date.today()
        context["daily_series"] = [
            {
                "date": (today - timedelta(days=29 - i)).strftime("%m-%d"),
                "count": daily_map.get(today - timedelta(days=29 - i), 0),
            }
            for i in range(30)
        ]
        context["recent_list"] = list(qs.order_by("-created_at")[:10])

        return PageProcessor().decorate(context, self.request)
