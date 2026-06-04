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


def _insurance_ctx(contract):
    def _obj(key):
        n = contract.nodes.filter(key=key).first()
        return n.get_object() if n else None

    insured_items = [
        n.get_object()
        for n in contract.nodes.filter(key__startswith="insured_item_").order_by("key")
        if n.get_object()
    ]
    insured_financial_assets = [
        n.get_object()
        for n in contract.nodes.filter(key__startswith="insured_fasset_").order_by("key")
        if n.get_object()
    ]

    return {
        "contract": contract,
        "insured_person": _obj("insured_person"),
        "payer_account": _obj("payer_acct"),
        "insurer_account": _obj("insurer_acct"),
        "premium_asset": _obj("premium_asset"),
        "insured_items": insured_items,
        "insured_financial_assets": insured_financial_assets,
        "premium_frequency": contract.metadata.get("premium_frequency", "—"),
        "premium_amount_base_units": contract.metadata.get("premium_amount_base_units"),
        "total_estimated_value": contract.metadata.get("total_estimated_value"),
    }


# ---------------------------------------------------------------------------
# Insurance — list
# ---------------------------------------------------------------------------

def insurance_list(request):
    qs = (
        Contract.objects
        .filter(metadata__archetype="insurance")
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

        rows.append({
            "contract": c,
            "insured_person": _obj("insured_person"),
            "premium_asset": _obj("premium_asset"),
            "item_count": (
                c.nodes.filter(key__startswith="insured_item_").count()
                + c.nodes.filter(key__startswith="insured_fasset_").count()
            ),
            "premium_frequency": c.metadata.get("premium_frequency", "—"),
            "premium_amount_base_units": c.metadata.get("premium_amount_base_units"),
        })

    return _render(request, "contracts/insurance_list.html", {"rows": rows, "q": q})


# ---------------------------------------------------------------------------
# Insurance — create
# ---------------------------------------------------------------------------

def insurance_create(request):
    from toto.contracts.forms import InsuranceCreateForm
    if request.method == "POST":
        form = InsuranceCreateForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            from toto.insurance.services import create_insurance_contract
            contract = create_insurance_contract(
                name=cd["name"],
                insured_person=cd["insured_person"],
                payer_account=cd["payer_account"],
                insurer_account=cd["insurer_account"],
                premium_asset=cd["premium_asset"],
                premium_amount_base_units=cd["premium_amount_base_units"],
                premium_frequency=cd["premium_frequency"],
                insured_items=list(cd["insured_items"] or []),
                insured_financial_assets=list(cd["insured_financial_assets"] or []),
            )
            messages.success(request, f"Insurance contract '{contract.name}' created.")
            return redirect("insurance:detail", uuid=contract.uuid)
    else:
        form = InsuranceCreateForm()
    return _render(request, "contracts/insurance_form.html", {"form": form})


# ---------------------------------------------------------------------------
# Insurance — detail
# ---------------------------------------------------------------------------

def insurance_detail(request, uuid):
    contract = get_object_or_404(Contract, uuid=uuid, metadata__archetype="insurance")
    ctx = _insurance_ctx(contract)

    signatories = list(contract.signatories.select_related("person").all())
    current_person = _get_person_for_request(request)
    ctx["signatories"] = signatories
    ctx["current_person_signatory"] = next(
        (s for s in signatories if current_person and s.person_id == current_person.pk), None
    )

    return _render(request, "contracts/insurance_detail.html", ctx)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class InsuranceMetricsView(LoginRequiredMixin, TemplateView):
    template_name = "insurance/metrics.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        from toto.assets.models import Obligation, ObligationStatus

        qs = Contract.objects.filter(metadata__archetype="insurance")
        total_contracts = qs.count()
        draft_contracts = qs.filter(status=Contract.STATUS_DRAFT).count()
        executed_contracts = qs.filter(status=Contract.STATUS_EXECUTED).count()

        week_ago = timezone.now() - timedelta(days=7)
        recent_contracts = qs.filter(created_at__gte=week_ago).count()

        contract_pks = list(qs.values_list("pk", flat=True))

        premium_ids = list(
            ContractNode.objects.filter(
                contract__in=contract_pks, node_type="obligation",
                key__regex=r"^premium_\d+$",
            ).values_list("object_id", flat=True)
        )
        claim_ids = list(
            ContractNode.objects.filter(
                contract__in=contract_pks, node_type="obligation",
                key__regex=r"^claim_\d+$",
            ).values_list("object_id", flat=True)
        )

        premium_qs = Obligation.objects.filter(pk__in=premium_ids)
        claim_qs = Obligation.objects.filter(pk__in=claim_ids)

        pending_premiums = premium_qs.filter(status=ObligationStatus.PENDING).count()
        settled_premiums = premium_qs.filter(status=ObligationStatus.FULFILLED).count()
        pending_claims = claim_qs.filter(status=ObligationStatus.PENDING).count()
        settled_claims = claim_qs.filter(status=ObligationStatus.FULFILLED).count()

        context.update({
            "total_contracts": total_contracts,
            "draft_contracts": draft_contracts,
            "executed_contracts": executed_contracts,
            "recent_contracts": recent_contracts,
            "pending_premiums": pending_premiums,
            "settled_premiums": settled_premiums,
            "pending_claims": pending_claims,
            "settled_claims": settled_claims,
        })

        context["status_series"] = list(
            qs.values("status").annotate(count=Count("id")).order_by("status")
        )
        context["premium_status_series"] = list(
            premium_qs.values("status").annotate(count=Count("id")).order_by("status")
        )
        context["claim_status_series"] = list(
            claim_qs.values("status").annotate(count=Count("id")).order_by("status")
        )
        context["type_series"] = [
            {"label": "Premiums", "pending": pending_premiums, "settled": settled_premiums},
            {"label": "Claims", "pending": pending_claims, "settled": settled_claims},
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
