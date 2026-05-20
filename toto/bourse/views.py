from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from toto.ui import PageProcessor

from toto.assets.models import LedgerAccount, LedgerTransaction

from .forms import ExchangeRequestCreateForm, ExchangeRequestResponseForm
from .models import AssetExchangeRequest, ExchangeRequestStatus
from .services import accept_exchange_request, cancel_exchange_request, reject_exchange_request


def bourse_render(request, template_name, context):
    return render(request, template_name, PageProcessor().decorate(context, request))


def _decorate_trade_tape(recent_trades, user):
    trade_refs = []
    for trade in recent_trades:
        trade.direction = "incoming" if trade.counterparty_id == user.id else "outgoing"
        trade_ref_items = [
            ("Offer", trade.offer_tx_reference),
            ("Expected", trade.request_tx_reference),
            ("Fee", trade.commission_tx_reference),
        ]
        trade.trade_ref_items = [(label, ref) for label, ref in trade_ref_items if ref]
        trade_refs.extend(ref for _, ref in trade.trade_ref_items)

    tx_by_ref = {
        tx.reference: tx
        for tx in LedgerTransaction.objects
        .filter(reference__in=trade_refs)
        .select_related("asset", "hash_record")
    }
    for trade in recent_trades:
        trade.trade_transactions = [
            {
                "label": label,
                "tx": tx_by_ref.get(ref),
                "reference": ref,
                "hash_record": getattr(tx_by_ref.get(ref), "hash_record", None),
            }
            for label, ref in trade.trade_ref_items
        ]


def _pair_stats(public_asks):
    pairs = {}
    for ask in public_asks:
        key = f"{ask.offer_asset.unit_name}/{ask.request_asset.unit_name}"
        item = pairs.setdefault(
            key,
            {
                "pair": key,
                "ask_count": 0,
                "last_rate": ask.exchange_rate,
                "offer_unit": ask.offer_asset.unit_name,
                "request_unit": ask.request_asset.unit_name,
                "offer_depth": Decimal("0"),
            },
        )
        item["ask_count"] += 1
        item["last_rate"] = ask.exchange_rate
        item["offer_depth"] += ask.offer_amount_display
    return sorted(pairs.values(), key=lambda item: (item["ask_count"], item["offer_depth"]), reverse=True)[:8]


@login_required
def exchange_center(request):
    incoming_qs = (
        AssetExchangeRequest.objects
        .filter(counterparty=request.user)
        .select_related("requester", "requester_account", "counterparty_account", "offer_asset", "request_asset")
        .order_by("-created_at")
    )
    outgoing_qs = (
        AssetExchangeRequest.objects
        .filter(requester=request.user)
        .select_related("counterparty", "requester_account", "counterparty_account", "offer_asset", "request_asset")
        .order_by("-created_at")
    )
    public_asks_qs = (
        AssetExchangeRequest.objects
        .filter(counterparty__isnull=True, status=ExchangeRequestStatus.PENDING)
        .exclude(requester=request.user)
        .select_related("requester", "requester_account", "offer_asset", "request_asset")
        .order_by("-created_at")
    )
    incoming = incoming_qs[:30]
    outgoing = outgoing_qs[:30]
    public_ask_sample = list(public_asks_qs[:50])
    public_asks = public_ask_sample[:24]
    proposals = sorted(
        list(incoming) + list(outgoing),
        key=lambda proposal: proposal.created_at,
        reverse=True,
    )
    for proposal in proposals:
        proposal.direction = "incoming" if proposal.counterparty_id == request.user.id else "outgoing"

    recent_trades = list(
        AssetExchangeRequest.objects
        .filter(
            Q(requester=request.user) | Q(counterparty=request.user),
            status=ExchangeRequestStatus.ACCEPTED,
        )
        .select_related("requester", "counterparty", "offer_asset", "request_asset")
        .order_by("-responded_at", "-updated_at")[:16]
    )
    _decorate_trade_tape(recent_trades, request.user)

    wallet_accounts = list(
        LedgerAccount.objects
        .filter(user=request.user, active=True)
        .prefetch_related("holdings__asset")
        .order_by("code")
    )
    for account in wallet_accounts:
        account.holding_preview = [
            holding for holding in account.holdings.all()
            if holding.balance_base_units > 0 and holding.asset.active
        ][:6]

    return bourse_render(request, "bourse/exchange_center.html", {
        "proposals": proposals,
        "public_asks": public_asks,
        "recent_trades": recent_trades,
        "incoming_requests": incoming,
        "outgoing_requests": outgoing,
        "wallet_accounts": wallet_accounts,
        "pair_stats": _pair_stats(public_ask_sample),
        "pending_incoming_count": incoming_qs.filter(status=ExchangeRequestStatus.PENDING).count(),
        "pending_outgoing_count": outgoing_qs.filter(status=ExchangeRequestStatus.PENDING).count(),
        "public_ask_count": public_asks_qs.count(),
        "recent_trade_count": len(recent_trades),
        "pending_status": ExchangeRequestStatus.PENDING,
    })


@login_required
def exchange_request_create(request):
    if request.method == "POST":
        form = ExchangeRequestCreateForm(request.POST, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Bourse proposal opened.")
            return redirect("bourse:exchange_center")
    else:
        form = ExchangeRequestCreateForm(user=request.user)
    return bourse_render(request, "bourse/exchange_request_form.html", {"form": form})


@login_required
def exchange_request_accept(request, pk):
    from toto.bazaar.wallet_pin import has_wallet_pin, session_is_verified

    exchange_request = get_object_or_404(
        AssetExchangeRequest.objects.select_related("counterparty", "requester", "offer_asset", "request_asset"),
        Q(counterparty=request.user) | (Q(counterparty__isnull=True) & ~Q(requester=request.user)),
        pk=pk,
        status=ExchangeRequestStatus.PENDING,
    )
    if request.method == "POST":
        form = ExchangeRequestResponseForm(request.POST, user=request.user, exchange_request=exchange_request)
        if form.is_valid():
            if not has_wallet_pin(request.user):
                messages.error(request, "Set a wallet PIN before accepting trades.")
                return redirect("assets:wallet_pin_set")
            if not session_is_verified(request.session):
                form.add_error(None, "Wallet PIN required. Please verify your PIN.")
            else:
                try:
                    accept_exchange_request(
                        exchange_request=exchange_request,
                        counterparty_account=form.cleaned_data["counterparty_account"],
                        response_note=form.cleaned_data.get("response_note", ""),
                    )
                except ValidationError as exc:
                    form.add_error(None, exc.messages[0] if hasattr(exc, "messages") else str(exc))
                else:
                    messages.success(request, "Trade accepted and settled on the ledger.")
                    return redirect("bourse:exchange_center")
    else:
        form = ExchangeRequestResponseForm(user=request.user, exchange_request=exchange_request)
    return bourse_render(request, "bourse/exchange_request_response.html", {
        "form": form,
        "exchange_request": exchange_request,
        "mode": "accept",
        "has_wallet_pin": has_wallet_pin(request.user) if request.user.is_authenticated else False,
    })


@require_POST
@login_required
def exchange_request_reject(request, pk):
    exchange_request = get_object_or_404(
        AssetExchangeRequest,
        Q(counterparty=request.user) | (Q(counterparty__isnull=True) & ~Q(requester=request.user)),
        pk=pk,
        status=ExchangeRequestStatus.PENDING,
    )
    reject_exchange_request(
        exchange_request=exchange_request,
        response_note=request.POST.get("response_note", ""),
    )
    messages.success(request, "Bourse proposal rejected.")
    return redirect("bourse:exchange_center")


@require_POST
@login_required
def exchange_request_cancel(request, pk):
    exchange_request = get_object_or_404(
        AssetExchangeRequest,
        pk=pk,
        requester=request.user,
        status=ExchangeRequestStatus.PENDING,
    )
    cancel_exchange_request(
        exchange_request=exchange_request,
        response_note=request.POST.get("response_note", ""),
    )
    messages.success(request, "Bourse proposal cancelled.")
    return redirect("bourse:exchange_center")
