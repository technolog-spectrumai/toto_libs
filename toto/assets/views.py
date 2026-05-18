from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from toto.ui import PageProcessor

from .hashing import verify_hash_chain
from .models import Asset, AssetHolding, LedgerAccount, LedgerEntry, LedgerTransaction, TransactionType
from .queries import list_asset_holders, verify_asset_ledger


def assets_render(request, template_name, context):
    return render(request, template_name, PageProcessor().decorate(context, request))


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------

def asset_list(request):
    assets = Asset.objects.all()
    return assets_render(request, "assets/asset_list.html", {
        "assets": assets,
        "asset_count": assets.count(),
        "active_count": assets.filter(active=True).count(),
    })


def asset_detail(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    holders = list_asset_holders(asset)
    recent_txs = LedgerTransaction.objects.filter(asset=asset).order_by("-created_at")[:20]
    ledger_status = verify_asset_ledger(asset)
    flow_data_url = reverse("assets:ledger_flow_data") + f"?asset={asset.unit_name}"
    flow_full_url = reverse("assets:ledger_flow") + f"?asset={asset.unit_name}"
    return assets_render(request, "assets/asset_detail.html", {
        "asset": asset,
        "holders": holders,
        "recent_txs": recent_txs,
        "ledger_status": ledger_status,
        "asset_flow_url": flow_data_url,
        "asset_flow_full_url": flow_full_url,
    })


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

def account_list(request):
    accounts = LedgerAccount.objects.all()
    return assets_render(request, "assets/account_list.html", {
        "accounts": accounts,
        "account_count": accounts.count(),
        "active_count": accounts.filter(active=True).count(),
    })


def account_detail(request, pk):
    account = get_object_or_404(LedgerAccount, pk=pk)
    holdings = AssetHolding.objects.filter(account=account).select_related("asset")
    recent_entries = (
        LedgerEntry.objects.filter(account=account)
        .select_related("transaction", "asset")
        .order_by("-created_at")[:30]
    )
    flow_data_url = reverse("assets:ledger_flow_data") + f"?account={account.code}"
    flow_full_url = reverse("assets:ledger_flow") + f"?account={account.code}"
    return assets_render(request, "assets/account_detail.html", {
        "account": account,
        "holdings": holdings,
        "recent_entries": recent_entries,
        "account_flow_url": flow_data_url,
        "account_flow_full_url": flow_full_url,
    })


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

def transaction_list(request):
    asset_filter = request.GET.get("asset")
    tx_type_filter = request.GET.get("type")

    txs = LedgerTransaction.objects.select_related("asset", "reversed_transaction").order_by("-created_at")
    if asset_filter:
        txs = txs.filter(asset__unit_name__iexact=asset_filter)
    if tx_type_filter:
        txs = txs.filter(transaction_type=tx_type_filter)

    assets = Asset.objects.all()
    from .models import TransactionType
    return assets_render(request, "assets/transaction_list.html", {
        "transactions": txs[:100],
        "assets": assets,
        "transaction_types": TransactionType.choices,
        "asset_filter": asset_filter or "",
        "tx_type_filter": tx_type_filter or "",
        "total_count": txs.count(),
    })


def transaction_detail(request, pk):
    tx = get_object_or_404(
        LedgerTransaction.objects.select_related("asset", "reversed_transaction"),
        pk=pk,
    )
    entries = tx.entries.select_related("account", "asset").order_by("created_at")
    hash_record = getattr(tx, "hash_record", None)
    try:
        hash_record = tx.hash_record
    except Exception:
        hash_record = None
    return assets_render(request, "assets/transaction_detail.html", {
        "tx": tx,
        "entries": entries,
        "hash_record": hash_record,
    })


# ---------------------------------------------------------------------------
# Chain verification
# ---------------------------------------------------------------------------

def chain_verify(request):
    valid = verify_hash_chain()
    from .models import LedgerHash
    hash_count = LedgerHash.objects.count()
    return assets_render(request, "assets/chain_verify.html", {
        "chain_valid": valid,
        "hash_count": hash_count,
    })


# ---------------------------------------------------------------------------
# Ledger flow graph
# ---------------------------------------------------------------------------

def ledger_flow(request):
    assets = Asset.objects.all()
    return assets_render(request, "assets/ledger_flow.html", {
        "assets": assets,
        "transaction_types": TransactionType.choices,
        "fetch_url": reverse("assets:ledger_flow_data"),
        "date_from": request.GET.get("date_from", ""),
        "date_to": request.GET.get("date_to", ""),
        "asset_filter": request.GET.get("asset", ""),
        "tx_type_filter": request.GET.get("type", ""),
    })


def ledger_flow_data(request):
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")
    asset_filter = request.GET.get("asset")
    tx_type_filter = request.GET.get("type")

    txs = LedgerTransaction.objects.filter(posted=True).prefetch_related(
        "entries__account", "entries__asset"
    ).select_related("asset", "reversed_transaction")

    account_filter = request.GET.get("account")

    if date_from:
        txs = txs.filter(created_at__date__gte=date_from)
    if date_to:
        txs = txs.filter(created_at__date__lte=date_to)
    if asset_filter:
        txs = txs.filter(asset__unit_name__iexact=asset_filter)
    if tx_type_filter:
        txs = txs.filter(transaction_type=tx_type_filter)
    if account_filter:
        txs = txs.filter(entries__account__code__iexact=account_filter).distinct()

    # Build account nodes from entries that appear in the filtered txs
    account_ids_seen = set()
    tx_list = list(txs[:200])

    nodes = {}
    edges = []

    TYPE_COLORS = {
        TransactionType.ASSET_CREATE:   "#22c55e",
        TransactionType.ASSET_TRANSFER: "#3b82f6",
        TransactionType.REVERSAL:       "#ef4444",
        TransactionType.ADJUSTMENT:     "#f59e0b",
    }

    for tx in tx_list:
        entries = list(tx.entries.all())
        senders = [e for e in entries if e.amount_base_units < 0]
        receivers = [e for e in entries if e.amount_base_units > 0]

        for e in entries:
            acc = e.account
            if acc.pk not in nodes:
                nodes[acc.pk] = {
                    "id": f"acc-{acc.pk}",
                    "label": acc.code,
                    "account_type": acc.account_type,
                    "url": reverse("assets:account_detail", args=[acc.pk]),
                }
            account_ids_seen.add(acc.pk)

        # Connect each sender→receiver pair through this transaction
        color = TYPE_COLORS.get(tx.transaction_type, "#6b7280")
        asset_label = tx.asset.unit_name if tx.asset else ""

        for s in senders:
            for r in receivers:
                amount = abs(s.amount_base_units)
                from .models import from_base_units
                amount_display = (
                    str(from_base_units(amount, s.asset.decimals))
                    if s.asset else str(amount)
                )
                edges.append({
                    "id": f"tx-{tx.pk}-{s.account_id}-{r.account_id}",
                    "source": f"acc-{s.account_id}",
                    "target": f"acc-{r.account_id}",
                    "label": f"{amount_display} {asset_label}",
                    "tx_type": tx.transaction_type,
                    "reference": tx.reference,
                    "color": color,
                    "url": reverse("assets:transaction_detail", args=[tx.pk]),
                    "created_at": tx.created_at.strftime("%Y-%m-%d %H:%M"),
                })

    return JsonResponse({
        "nodes": list(nodes.values()),
        "edges": edges,
    })
