from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

try:
    from toto.ui import PageProcessor
except Exception:  # pragma: no cover
    PageProcessor = None

from .forms import (
    AmortizationContractForm,
    AmortizationEntryForm,
    EscrowContractForm,
    FinancialInstrumentForm,
    ForwardContractForm,
    FutureContractForm,
    FutureMarketForm,
    LeaseContractForm,
    OptionContractForm,
    RevenueShareContractForm,
    RevenueShareRecipientInlineForm,
    StakingPositionForm,
    SubscriptionContractForm,
    VestingContractForm,
)
from .models import (
    AmortizationContract,
    EscrowContract,
    FinancialInstrument,
    ForwardContract,
    FutureMarket,
    InstrumentStatus,
    InstrumentType,
    LeaseContract,
    OptionContract,
    RevenueShareRecipient,
    StakingPosition,
    SubscriptionContract,
)
from .queries import dashboard_counts, list_instruments
from .services import AmortizationService, EscrowService, ForwardService, LeaseService, OptionService, StakingService, SubscriptionService


def instruments_render(request, template_name, context):
    if PageProcessor:
        context = PageProcessor().decorate(context, request)
    return render(request, template_name, context)


def _create_instrument_and_contract(form, instrument_type, user):
    """Auto-create a FinancialInstrument then attach and save the contract form."""
    with transaction.atomic():
        instrument = FinancialInstrument.objects.create(
            reference=form.cleaned_data["name"],
            instrument_type=instrument_type,
            status=InstrumentStatus.DRAFT,
            issuer=user if user.is_authenticated else None,
        )
        contract = form.save(commit=False)
        contract.instrument = instrument
        contract.save()
    return instrument


def instrument_list(request):
    instrument_type = request.GET.get("type") or None
    status = request.GET.get("status") or None
    instruments = list_instruments(instrument_type=instrument_type, status=status)
    return instruments_render(request, "instruments/instrument_list.html", {
        "instruments": instruments[:100],
        "counts": dashboard_counts(),
        "instrument_type": instrument_type,
        "status": status,
        "status_choices": InstrumentStatus.choices,
        "type_choices": InstrumentType.choices,
    })


def instrument_detail(request, pk):
    instrument = get_object_or_404(
        FinancialInstrument.objects.select_related("issuer", "contract_account"),
        pk=pk,
    )
    ctx = {"instrument": instrument}
    if hasattr(instrument, "revenue_share_contract"):
        ctx["recipient_form"] = RevenueShareRecipientInlineForm()
    return instruments_render(request, "instruments/instrument_detail.html", ctx)


@login_required
def instrument_create(request):
    if request.method == "POST":
        form = FinancialInstrumentForm(request.POST)
        if form.is_valid():
            instrument = form.save()
            messages.success(request, "Instrument created.")
            return redirect("instruments:instrument_detail", pk=instrument.pk)
    else:
        form = FinancialInstrumentForm(initial={"issuer": request.user})
    return instruments_render(request, "instruments/instrument_form.html", {"form": form})


# ── Escrow ────────────────────────────────────────────────────────────────────

@login_required
def escrow_create(request):
    if request.method == "POST":
        form = EscrowContractForm(request.POST)
        if form.is_valid():
            instrument = _create_instrument_and_contract(form, InstrumentType.ESCROW, request.user)
            messages.success(request, "Escrow created.")
            return redirect("instruments:instrument_detail", pk=instrument.pk)
    else:
        form = EscrowContractForm()
    return instruments_render(request, "instruments/escrow_form.html", {"form": form})


@require_POST
@login_required
def escrow_fund(request, pk):
    escrow = get_object_or_404(EscrowContract.objects.select_related("instrument", "asset"), pk=pk)
    try:
        EscrowService.fund(escrow)
        messages.success(request, "Escrow funded.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("instruments:instrument_detail", pk=escrow.instrument_id)


@require_POST
@login_required
def escrow_release(request, pk):
    escrow = get_object_or_404(EscrowContract.objects.select_related("instrument", "asset"), pk=pk)
    try:
        EscrowService.release(escrow)
        messages.success(request, "Escrow released.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("instruments:instrument_detail", pk=escrow.instrument_id)


@require_POST
@login_required
def escrow_refund(request, pk):
    escrow = get_object_or_404(EscrowContract.objects.select_related("instrument", "asset"), pk=pk)
    try:
        EscrowService.refund(escrow)
        messages.success(request, "Escrow refunded.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("instruments:instrument_detail", pk=escrow.instrument_id)


# ── Forward ───────────────────────────────────────────────────────────────────

@login_required
def forward_create(request):
    if request.method == "POST":
        form = ForwardContractForm(request.POST)
        if form.is_valid():
            instrument = _create_instrument_and_contract(form, InstrumentType.FORWARD, request.user)
            messages.success(request, "Forward contract created.")
            return redirect("instruments:instrument_detail", pk=instrument.pk)
    else:
        form = ForwardContractForm()
    return instruments_render(request, "instruments/forward_form.html", {"form": form})


@require_POST
@login_required
def forward_activate(request, pk):
    instrument = get_object_or_404(
        FinancialInstrument.objects.select_related("forward_contract"),
        pk=pk,
    )
    fwd = getattr(instrument, "forward_contract", None)
    if not fwd:
        messages.error(request, "No forward contract attached to this instrument.")
        return redirect("instruments:instrument_detail", pk=pk)
    try:
        ForwardService.activate(fwd)
        messages.success(request, "Forward contract activated.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("instruments:instrument_detail", pk=pk)


# ── Future markets ────────────────────────────────────────────────────────────

def future_market_list(request):
    markets = FutureMarket.objects.select_related("underlying_asset", "settlement_asset").order_by("code")
    return instruments_render(request, "instruments/future_market_list.html", {"markets": markets})


@login_required
def future_market_create(request):
    if request.method == "POST":
        form = FutureMarketForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Future market created.")
            return redirect("instruments:future_market_list")
    else:
        form = FutureMarketForm()
    return instruments_render(request, "instruments/future_market_form.html", {"form": form})


@login_required
def future_contract_create(request):
    if request.method == "POST":
        form = FutureContractForm(request.POST)
        if form.is_valid():
            instrument = _create_instrument_and_contract(form, InstrumentType.FUTURE, request.user)
            messages.success(request, "Future contract created.")
            return redirect("instruments:instrument_detail", pk=instrument.pk)
    else:
        form = FutureContractForm()
    return instruments_render(request, "instruments/future_contract_form.html", {"form": form})


# ── Option ────────────────────────────────────────────────────────────────────

@login_required
def option_create(request):
    if request.method == "POST":
        form = OptionContractForm(request.POST)
        if form.is_valid():
            instrument = _create_instrument_and_contract(form, InstrumentType.OPTION, request.user)
            messages.success(request, "Option contract created.")
            return redirect("instruments:instrument_detail", pk=instrument.pk)
    else:
        form = OptionContractForm()
    return instruments_render(request, "instruments/option_form.html", {"form": form})


@require_POST
@login_required
def option_exercise(request, pk):
    instrument = get_object_or_404(
        FinancialInstrument.objects.select_related("option_contract__underlying_asset"),
        pk=pk,
    )
    opt = getattr(instrument, "option_contract", None)
    if not opt:
        messages.error(request, "No option contract attached.")
        return redirect("instruments:instrument_detail", pk=pk)
    try:
        OptionService.exercise(opt)
        messages.success(request, "Option exercised.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("instruments:instrument_detail", pk=pk)


# ── Vesting ───────────────────────────────────────────────────────────────────

@login_required
def vesting_create(request):
    if request.method == "POST":
        form = VestingContractForm(request.POST)
        if form.is_valid():
            instrument = _create_instrument_and_contract(form, InstrumentType.VESTING, request.user)
            messages.success(request, "Vesting contract created.")
            return redirect("instruments:instrument_detail", pk=instrument.pk)
    else:
        form = VestingContractForm()
    return instruments_render(request, "instruments/vesting_form.html", {"form": form})


# ── Revenue Share ─────────────────────────────────────────────────────────────

@login_required
def revenue_share_create(request):
    if request.method == "POST":
        form = RevenueShareContractForm(request.POST)
        if form.is_valid():
            instrument = _create_instrument_and_contract(form, InstrumentType.REVENUE_SHARE, request.user)
            messages.success(request, "Revenue share contract created.")
            return redirect("instruments:instrument_detail", pk=instrument.pk)
    else:
        form = RevenueShareContractForm()
    return instruments_render(request, "instruments/revenue_share_form.html", {"form": form})


@require_POST
@login_required
def recipient_add(request, pk):
    instrument = get_object_or_404(FinancialInstrument, pk=pk)
    rev = getattr(instrument, "revenue_share_contract", None)
    if rev is None:
        messages.error(request, "This instrument has no revenue share contract.")
        return redirect("instruments:instrument_detail", pk=pk)
    form = RevenueShareRecipientInlineForm(request.POST)
    if form.is_valid():
        total_allocated = sum(r.share_bps for r in rev.recipients.all())
        new_bps = form.get_share_bps()
        if total_allocated + new_bps > 10000:
            messages.error(request, f"Total share would exceed 100% ({(total_allocated + new_bps) / 100:.2f}%). Remove or reduce another recipient first.")
        else:
            RevenueShareRecipient.objects.create(
                contract=rev,
                account=form.cleaned_data["account"],
                share_bps=new_bps,
            )
            messages.success(request, f"Recipient added ({form.cleaned_data['share_percent']}%).")
    else:
        messages.error(request, "Invalid recipient data: " + "; ".join(
            f"{f}: {', '.join(e)}" for f, e in form.errors.items()
        ))
    return redirect("instruments:instrument_detail", pk=pk)


@require_POST
@login_required
def recipient_remove(request, pk, recipient_pk):
    instrument = get_object_or_404(FinancialInstrument, pk=pk)
    recipient = get_object_or_404(RevenueShareRecipient, pk=recipient_pk, contract__instrument=instrument)
    recipient.delete()
    messages.success(request, "Recipient removed.")
    return redirect("instruments:instrument_detail", pk=pk)


# ── Staking ───────────────────────────────────────────────────────────────────

@login_required
def staking_create(request):
    if request.method == "POST":
        form = StakingPositionForm(request.POST)
        if form.is_valid():
            instrument = _create_instrument_and_contract(form, InstrumentType.STAKING, request.user)
            messages.success(request, "Staking position created.")
            return redirect("instruments:instrument_detail", pk=instrument.pk)
    else:
        form = StakingPositionForm()
    return instruments_render(request, "instruments/staking_form.html", {"form": form})


@require_POST
@login_required
def staking_stake(request, pk):
    instrument = get_object_or_404(
        FinancialInstrument.objects.select_related("staking_position__staked_asset"),
        pk=pk,
    )
    stk = getattr(instrument, "staking_position", None)
    if not stk:
        messages.error(request, "No staking position attached.")
        return redirect("instruments:instrument_detail", pk=pk)
    try:
        StakingService.stake(stk)
        messages.success(request, "Assets staked.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("instruments:instrument_detail", pk=pk)


@require_POST
@login_required
def staking_unstake(request, pk):
    instrument = get_object_or_404(
        FinancialInstrument.objects.select_related("staking_position__staked_asset"),
        pk=pk,
    )
    stk = getattr(instrument, "staking_position", None)
    if not stk:
        messages.error(request, "No staking position attached.")
        return redirect("instruments:instrument_detail", pk=pk)
    try:
        StakingService.unstake(stk)
        messages.success(request, "Assets unstaked.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("instruments:instrument_detail", pk=pk)


# ── Subscription ──────────────────────────────────────────────────────────────

@login_required
def subscription_create(request):
    if request.method == "POST":
        form = SubscriptionContractForm(request.POST)
        if form.is_valid():
            instrument = _create_instrument_and_contract(form, InstrumentType.SUBSCRIPTION, request.user)
            messages.success(request, "Subscription created.")
            return redirect("instruments:instrument_detail", pk=instrument.pk)
    else:
        form = SubscriptionContractForm()
    return instruments_render(request, "instruments/subscription_form.html", {"form": form})


@require_POST
@login_required
def subscription_activate(request, pk):
    instrument = get_object_or_404(
        FinancialInstrument.objects.select_related("subscription_contract__asset"),
        pk=pk,
    )
    sub = getattr(instrument, "subscription_contract", None)
    if not sub:
        messages.error(request, "No subscription contract attached.")
        return redirect("instruments:instrument_detail", pk=pk)
    try:
        SubscriptionService.activate(sub)
        messages.success(request, "Subscription activated.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("instruments:instrument_detail", pk=pk)


@require_POST
@login_required
def subscription_cancel(request, pk):
    instrument = get_object_or_404(
        FinancialInstrument.objects.select_related("subscription_contract"),
        pk=pk,
    )
    sub = getattr(instrument, "subscription_contract", None)
    if not sub:
        messages.error(request, "No subscription contract attached.")
        return redirect("instruments:instrument_detail", pk=pk)
    at_period_end = request.POST.get("at_period_end", "1") != "0"
    try:
        SubscriptionService.cancel(sub, at_period_end=at_period_end)
        msg = "Subscription will cancel at period end." if at_period_end else "Subscription cancelled immediately."
        messages.success(request, msg)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("instruments:instrument_detail", pk=pk)


@require_POST
@login_required
def subscription_pause(request, pk):
    instrument = get_object_or_404(
        FinancialInstrument.objects.select_related("subscription_contract"),
        pk=pk,
    )
    sub = getattr(instrument, "subscription_contract", None)
    if not sub:
        messages.error(request, "No subscription contract attached.")
        return redirect("instruments:instrument_detail", pk=pk)
    try:
        SubscriptionService.pause(sub)
        messages.success(request, "Subscription paused.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("instruments:instrument_detail", pk=pk)


@require_POST
@login_required
def subscription_resume(request, pk):
    instrument = get_object_or_404(
        FinancialInstrument.objects.select_related("subscription_contract"),
        pk=pk,
    )
    sub = getattr(instrument, "subscription_contract", None)
    if not sub:
        messages.error(request, "No subscription contract attached.")
        return redirect("instruments:instrument_detail", pk=pk)
    try:
        SubscriptionService.resume(sub)
        messages.success(request, "Subscription resumed.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("instruments:instrument_detail", pk=pk)


# ── Lease ──────────────────────────────────────────────────────────────────

@login_required
def lease_list(request):
    status = request.GET.get("status") or None
    qs = LeaseContract.objects.select_related(
        "instrument", "lessee_account", "lessor_account", "payment_asset", "leased_asset"
    ).order_by("-created_at")
    if status:
        qs = qs.filter(status=status)
    from .models import LeaseStatus
    return instruments_render(request, "instruments/lease_list.html", {
        "leases": qs[:100],
        "status": status,
        "status_choices": LeaseStatus.choices,
        "total": LeaseContract.objects.count(),
        "active_count": LeaseContract.objects.filter(status="active").count(),
        "draft_count": LeaseContract.objects.filter(status="draft").count(),
    })


@login_required
def lease_detail(request, pk):
    lease = get_object_or_404(
        LeaseContract.objects.select_related(
            "instrument", "lessee_account", "lessor_account",
            "leased_asset", "payment_asset", "revenue_account",
        ),
        pk=pk,
    )
    executions = lease.instrument.executions.all()
    return instruments_render(request, "instruments/lease_detail.html", {
        "lease": lease,
        "executions": executions,
    })


@login_required
def lease_create(request):
    if request.method == "POST":
        form = LeaseContractForm(request.POST)
        if form.is_valid():
            instrument = _create_instrument_and_contract(form, InstrumentType.LEASE, request.user)
            messages.success(request, "Lease created.")
            return redirect("instruments:lease_detail", pk=instrument.lease_contract.pk)
    else:
        form = LeaseContractForm()
    return instruments_render(request, "instruments/lease_form.html", {"form": form})


@require_POST
@login_required
def lease_activate(request, pk):
    lease = get_object_or_404(
        LeaseContract.objects.select_related("instrument"),
        pk=pk,
    )
    try:
        LeaseService.activate(lease)
        messages.success(request, "Lease activated.")
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect("instruments:lease_detail", pk=lease.pk)


@require_POST
@login_required
def lease_cancel(request, pk):
    lease = get_object_or_404(
        LeaseContract.objects.select_related("instrument"),
        pk=pk,
    )
    try:
        LeaseService.cancel(lease)
        messages.success(request, "Lease cancelled.")
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect("instruments:lease_detail", pk=lease.pk)


@require_POST
@login_required
def lease_charge_fixed(request, pk):
    lease = get_object_or_404(
        LeaseContract.objects.select_related(
            "instrument", "payment_asset", "lessee_account", "revenue_account"
        ),
        pk=pk,
    )
    try:
        LeaseService.charge_fixed_fee(lease)
        messages.success(request, "Fixed fee charged.")
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect("instruments:lease_detail", pk=lease.pk)


# ── Amortization ──────────────────────────────────────────────────────────────

@login_required
def amortization_list(request):
    from .models import AmortizationStatus
    status = request.GET.get("status") or None
    qs = AmortizationContract.objects.select_related(
        "instrument", "source_account", "destination_account", "asset"
    ).order_by("-created_at")
    if status:
        qs = qs.filter(status=status)
    return instruments_render(request, "instruments/amortization_list.html", {
        "contracts": qs[:100],
        "status": status,
        "status_choices": AmortizationStatus.choices,
        "total": AmortizationContract.objects.count(),
        "active_count": AmortizationContract.objects.filter(status="active").count(),
        "draft_count": AmortizationContract.objects.filter(status="draft").count(),
    })


@login_required
def amortization_detail(request, pk):
    contract = get_object_or_404(
        AmortizationContract.objects.select_related(
            "instrument", "source_account", "destination_account", "asset"
        ),
        pk=pk,
    )
    entries = contract.entries.select_related("transaction").order_by("-created_at")
    executions = contract.instrument.executions.all()
    return instruments_render(request, "instruments/amortization_detail.html", {
        "contract": contract,
        "entries": entries,
        "executions": executions,
    })


@login_required
def amortization_create(request):
    if request.method == "POST":
        form = AmortizationContractForm(request.POST)
        if form.is_valid():
            instrument = _create_instrument_and_contract(form, InstrumentType.AMORTIZATION, request.user)
            messages.success(request, "Amortization contract created.")
            return redirect("instruments:amortization_detail", pk=instrument.amortization_contract.pk)
    else:
        form = AmortizationContractForm()
    return instruments_render(request, "instruments/amortization_form.html", {"form": form})


@require_POST
@login_required
def amortization_activate(request, pk):
    contract = get_object_or_404(
        AmortizationContract.objects.select_related("instrument"),
        pk=pk,
    )
    try:
        AmortizationService.activate(contract)
        messages.success(request, "Contract activated.")
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect("instruments:amortization_detail", pk=pk)


@require_POST
@login_required
def amortization_pause(request, pk):
    contract = get_object_or_404(
        AmortizationContract.objects.select_related("instrument"),
        pk=pk,
    )
    try:
        AmortizationService.pause(contract)
        messages.success(request, "Contract paused.")
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect("instruments:amortization_detail", pk=pk)


@require_POST
@login_required
def amortization_resume(request, pk):
    contract = get_object_or_404(
        AmortizationContract.objects.select_related("instrument"),
        pk=pk,
    )
    try:
        AmortizationService.resume(contract)
        messages.success(request, "Contract resumed.")
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect("instruments:amortization_detail", pk=pk)


@require_POST
@login_required
def amortization_cancel(request, pk):
    contract = get_object_or_404(
        AmortizationContract.objects.select_related("instrument"),
        pk=pk,
    )
    try:
        AmortizationService.cancel(contract)
        messages.success(request, "Contract cancelled.")
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect("instruments:amortization_detail", pk=pk)


@login_required
def amortization_amortize(request, pk):
    contract = get_object_or_404(
        AmortizationContract.objects.select_related(
            "instrument", "asset", "source_account", "destination_account"
        ),
        pk=pk,
    )
    if request.method == "POST":
        form = AmortizationEntryForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    AmortizationService.amortize(
                        contract=contract,
                        amount_base_units=form.cleaned_data["amount_base_units"],
                        source_type=form.cleaned_data.get("source_type", ""),
                        source_id=form.cleaned_data.get("source_id", ""),
                    )
                messages.success(request, "Amortization entry recorded.")
                return redirect("instruments:amortization_detail", pk=pk)
            except Exception as exc:
                messages.error(request, str(exc))
    else:
        form = AmortizationEntryForm()
    return instruments_render(request, "instruments/amortization_entry_form.html", {
        "form": form,
        "contract": contract,
    })
