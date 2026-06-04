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


def _payroll_ctx(contract):
    def _obj(key):
        n = contract.nodes.filter(key=key).first()
        return n.get_object() if n else None

    return {
        "contract": contract,
        "schedule": _obj("pay_schedule"),
        "allocation": _obj("budget"),
        "worker": _obj("worker_person"),
        "asset": _obj("payment_asset"),
        "payer_account": _obj("payer_acct"),
        "worker_account": _obj("worker_acct"),
    }


def _payroll_duties(contract):
    duty_nodes = contract.nodes.filter(node_type="obligation").order_by("key")
    duties = []
    for node in duty_nodes:
        obligation = node.get_object()
        if not obligation:
            continue
        gates_edge = (
            contract.edges
            .filter(target=node, edge_type="gates")
            .select_related("source")
            .first()
        )
        condition = gates_edge.source.get_object() if gates_edge else None
        duties.append({
            "node": node,
            "obligation": obligation,
            "condition": condition,
        })
    return duties


# ---------------------------------------------------------------------------
# Payroll — list
# ---------------------------------------------------------------------------

def payroll_list(request):
    qs = (
        Contract.objects
        .filter(metadata__archetype="payroll")
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
            "worker": _obj("worker_person"),
            "asset": _obj("payment_asset"),
            "allocation": _obj("budget"),
            "duty_count": c.nodes.filter(node_type="obligation").count(),
        })

    return _render(request, "contracts/payroll_list.html", {"rows": rows, "q": q})


# ---------------------------------------------------------------------------
# Payroll — create
# ---------------------------------------------------------------------------

def payroll_create(request):
    from toto.contracts.forms import PayrollCreateForm
    if request.method == "POST":
        form = PayrollCreateForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            meta = {k: cd[k] for k in ("source_app", "source_model", "source_id") if cd.get(k)}
            from toto.payroll.services import create_payroll_contract
            contract = create_payroll_contract(
                name=cd["name"],
                payer_account=cd["payer_account"],
                worker_person=cd["worker_person"],
                worker_account=cd["worker_account"],
                asset=cd["asset"],
                amount_base_units=cd["amount_base_units"],
                frequency=cd["frequency"],
                metadata=meta or None,
            )
            messages.success(request, f"Payroll contract '{contract.name}' created.")
            return redirect("payroll:detail", uuid=contract.uuid)
    else:
        from toto.contracts.forms import PayrollCreateForm
        form = PayrollCreateForm()
    return _render(request, "contracts/payroll_form.html", {"form": form})


# ---------------------------------------------------------------------------
# Payroll — detail
# ---------------------------------------------------------------------------

def payroll_detail(request, uuid):
    contract = get_object_or_404(Contract, uuid=uuid, metadata__archetype="payroll")
    ctx = _payroll_ctx(contract)
    ctx["duties"] = _payroll_duties(contract)

    from toto.claims.models import ContractEvent
    ctx["events"] = list(
        ContractEvent.objects
        .filter(source_type="contracts.Contract", source_id=str(contract.pk))
        .order_by("-created_at")[:30]
    )

    signatories = list(contract.signatories.select_related("person").all())
    current_person = _get_person_for_request(request)
    ctx["signatories"] = signatories
    ctx["current_person_signatory"] = next(
        (s for s in signatories if current_person and s.person_id == current_person.pk), None
    )

    return _render(request, "contracts/payroll_detail.html", ctx)


# ---------------------------------------------------------------------------
# Payroll — add duty
# ---------------------------------------------------------------------------

def payroll_duty_create(request, uuid):
    contract = get_object_or_404(Contract, uuid=uuid, metadata__archetype="payroll")
    ctx = _payroll_ctx(contract)

    if request.method == "POST":
        from toto.contracts.forms import PayrollDutyForm
        form = PayrollDutyForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            meta = {k: cd[k] for k in ("source_app", "source_id") if cd.get(k)}
            from toto.payroll.services import create_payroll_duty
            try:
                create_payroll_duty(
                    contract=contract,
                    worker_person=ctx["worker"],
                    worker_account=ctx["worker_account"],
                    amount_base_units=cd["amount_base_units"],
                    due_at=cd.get("due_at"),
                    metadata=meta or None,
                )
                messages.success(request, "Duty created.")
                return redirect("payroll:detail", uuid=uuid)
            except Exception as exc:
                messages.error(request, f"Could not create duty: {exc}")
    else:
        from toto.contracts.forms import PayrollDutyForm
        form = PayrollDutyForm()

    ctx["form"] = form
    return _render(request, "contracts/payroll_duty_form.html", ctx)


# ---------------------------------------------------------------------------
# Payroll — duty actions (POST-only)
# ---------------------------------------------------------------------------

def payroll_duty_mark_due(request, uuid, obligation_pk):
    contract = get_object_or_404(Contract, uuid=uuid, metadata__archetype="payroll")
    if request.method == "POST":
        node = get_object_or_404(
            ContractNode, contract=contract, node_type="obligation",
            object_id=str(obligation_pk),
        )
        from toto.payroll.services import mark_payroll_due
        try:
            mark_payroll_due(contract=contract, duty_node_key=node.key)
            messages.success(request, "Duty marked as due.")
        except Exception as exc:
            messages.error(request, f"Error: {exc}")
    return redirect("payroll:detail", uuid=uuid)


def payroll_duty_approve(request, uuid, obligation_pk):
    contract = get_object_or_404(Contract, uuid=uuid, metadata__archetype="payroll")
    if request.method == "POST":
        node = get_object_or_404(
            ContractNode, contract=contract, node_type="obligation",
            object_id=str(obligation_pk),
        )
        gates_edge = (
            contract.edges
            .filter(target=node, edge_type="gates")
            .select_related("source")
            .first()
        )
        if not gates_edge:
            messages.error(request, "No approval condition found for this duty.")
            return redirect("payroll:detail", uuid=uuid)
        condition = gates_edge.source.get_object()
        from toto.payroll.services import approve_payroll_duty
        try:
            approve_payroll_duty(
                condition=condition,
                approver=request.user if request.user.is_authenticated else None,
            )
            messages.success(request, "Duty approved.")
        except Exception as exc:
            messages.error(request, f"Error: {exc}")
    return redirect("payroll:detail", uuid=uuid)


def payroll_duty_settle(request, uuid, obligation_pk):
    contract = get_object_or_404(Contract, uuid=uuid, metadata__archetype="payroll")
    if request.method == "POST":
        from toto.assets.models import Obligation
        obligation = get_object_or_404(Obligation, pk=obligation_pk)
        from toto.payroll.services import settle_payroll_duty
        try:
            settle_payroll_duty(obligation=obligation, contract=contract)
            messages.success(request, "Duty settled.")
        except Exception as exc:
            messages.error(request, f"Settlement failed: {exc}")
    return redirect("payroll:detail", uuid=uuid)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class PayrollMetricsView(LoginRequiredMixin, TemplateView):
    template_name = "payroll/metrics.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        from toto.assets.models import Obligation, ObligationStatus

        qs = Contract.objects.filter(metadata__archetype="payroll")
        total_contracts = qs.count()
        draft_contracts = qs.filter(status=Contract.STATUS_DRAFT).count()
        executed_contracts = qs.filter(status=Contract.STATUS_EXECUTED).count()

        week_ago = timezone.now() - timedelta(days=7)
        recent_contracts = qs.filter(created_at__gte=week_ago).count()

        obls = Obligation.objects.filter(order_reference__startswith="payroll:")
        total_duties = obls.count()
        pending_duties = obls.filter(status=ObligationStatus.PENDING).count()
        settled_duties = obls.filter(status=ObligationStatus.FULFILLED).count()

        context.update({
            "total_contracts": total_contracts,
            "draft_contracts": draft_contracts,
            "executed_contracts": executed_contracts,
            "recent_contracts": recent_contracts,
            "total_duties": total_duties,
            "pending_duties": pending_duties,
            "settled_duties": settled_duties,
        })

        context["status_series"] = list(
            qs.values("status").annotate(count=Count("id")).order_by("status")
        )
        context["duty_status_series"] = list(
            obls.values("status").annotate(count=Count("id")).order_by("status")
        )

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
