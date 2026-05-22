from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

try:
    from toto.ui import PageProcessor
except Exception:  # pragma: no cover
    PageProcessor = None

from .forms import (
    EscrowContractForm,
    FinancialInstrumentForm,
    ForwardContractForm,
    FutureContractForm,
    FutureMarketForm,
    RevenueShareContractForm,
    StakingPositionForm,
    TimelockContractForm,
    VestingContractForm,
)
from .models import (
    EscrowContract,
    FinancialInstrument,
    ForwardContract,
    FutureMarket,
    InstrumentStatus,
    InstrumentType,
    StakingPosition,
    TimelockContract,
)
from .queries import dashboard_counts, list_instruments
from .services import EscrowService, ForwardService, StakingService, TimelockService


def instruments_render(request, template_name, context):
    if PageProcessor:
        context = PageProcessor().decorate(context, request)
    return render(request, template_name, context)


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
    return instruments_render(request, "instruments/instrument_detail.html", {"instrument": instrument})


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
    return instruments_render(request, "instruments/form.html", {"form": form, "title": "Create Instrument"})


# ── Escrow ────────────────────────────────────────────────────────────────────

@login_required
def escrow_create(request):
    if request.method == "POST":
        form = EscrowContractForm(request.POST)
        if form.is_valid():
            escrow = form.save()
            messages.success(request, "Escrow created.")
            return redirect("instruments:instrument_detail", pk=escrow.instrument_id)
    else:
        form = EscrowContractForm()
    return instruments_render(request, "instruments/form.html", {"form": form, "title": "Create Escrow"})


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
            fwd = form.save()
            messages.success(request, "Forward contract created.")
            return redirect("instruments:instrument_detail", pk=fwd.instrument_id)
    else:
        form = ForwardContractForm()
    return instruments_render(request, "instruments/form.html", {"form": form, "title": "Create Forward Contract"})


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
    return instruments_render(request, "instruments/form.html", {"form": form, "title": "Create Future Market"})


@login_required
def future_contract_create(request):
    if request.method == "POST":
        form = FutureContractForm(request.POST)
        if form.is_valid():
            fc = form.save()
            messages.success(request, "Future contract created.")
            return redirect("instruments:instrument_detail", pk=fc.instrument_id)
    else:
        form = FutureContractForm()
    return instruments_render(request, "instruments/form.html", {"form": form, "title": "Create Future Contract"})


# ── Timelock ──────────────────────────────────────────────────────────────────

@login_required
def timelock_create(request):
    if request.method == "POST":
        form = TimelockContractForm(request.POST)
        if form.is_valid():
            tl = form.save()
            messages.success(request, "Timelock created.")
            return redirect("instruments:instrument_detail", pk=tl.instrument_id)
    else:
        form = TimelockContractForm()
    return instruments_render(request, "instruments/form.html", {"form": form, "title": "Create Timelock"})


@require_POST
@login_required
def timelock_fund(request, pk):
    instrument = get_object_or_404(
        FinancialInstrument.objects.select_related("timelock_contract"),
        pk=pk,
    )
    tl = getattr(instrument, "timelock_contract", None)
    if not tl:
        messages.error(request, "No timelock contract attached.")
        return redirect("instruments:instrument_detail", pk=pk)
    try:
        TimelockService.fund(tl)
        messages.success(request, "Timelock funded.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("instruments:instrument_detail", pk=pk)


@require_POST
@login_required
def timelock_release(request, pk):
    instrument = get_object_or_404(
        FinancialInstrument.objects.select_related("timelock_contract__asset"),
        pk=pk,
    )
    tl = getattr(instrument, "timelock_contract", None)
    if not tl:
        messages.error(request, "No timelock contract attached.")
        return redirect("instruments:instrument_detail", pk=pk)
    try:
        TimelockService.release(tl)
        messages.success(request, "Timelock released.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("instruments:instrument_detail", pk=pk)


# ── Vesting ───────────────────────────────────────────────────────────────────

@login_required
def vesting_create(request):
    if request.method == "POST":
        form = VestingContractForm(request.POST)
        if form.is_valid():
            vc = form.save()
            messages.success(request, "Vesting contract created.")
            return redirect("instruments:instrument_detail", pk=vc.instrument_id)
    else:
        form = VestingContractForm()
    return instruments_render(request, "instruments/form.html", {"form": form, "title": "Create Vesting Contract"})


# ── Revenue Share ─────────────────────────────────────────────────────────────

@login_required
def revenue_share_create(request):
    if request.method == "POST":
        form = RevenueShareContractForm(request.POST)
        if form.is_valid():
            rev = form.save()
            messages.success(request, "Revenue share contract created.")
            return redirect("instruments:instrument_detail", pk=rev.instrument_id)
    else:
        form = RevenueShareContractForm()
    return instruments_render(request, "instruments/form.html", {"form": form, "title": "Create Revenue Share"})


# ── Staking ───────────────────────────────────────────────────────────────────

@login_required
def staking_create(request):
    if request.method == "POST":
        form = StakingPositionForm(request.POST)
        if form.is_valid():
            stk = form.save()
            messages.success(request, "Staking position created.")
            return redirect("instruments:instrument_detail", pk=stk.instrument_id)
    else:
        form = StakingPositionForm()
    return instruments_render(request, "instruments/form.html", {"form": form, "title": "Create Staking Position"})


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
