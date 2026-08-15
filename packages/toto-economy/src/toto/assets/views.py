from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from toto.ui import PageProcessor

from .hashing import verify_hash_chain
from .models import (
    AccountType,
    Asset,
    AssetHolding,
    LedgerAccount,
    LedgerEntry,
    LedgerTransaction,
    TransactionType,
)
from .queries import list_asset_holders, verify_asset_ledger
from .services.assets import distribute_asset


def assets_render(request, template_name, context):
    return render(request, template_name, PageProcessor().decorate(context, request))


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------

@login_required
def asset_create(request):
    """Mint a new asset type with a fixed total supply. Staff only."""
    if not request.user.is_staff:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    ledger_accounts = LedgerAccount.objects.filter(active=True).order_by("code")

    if request.method == "POST":
        import uuid as _uuid
        from decimal import InvalidOperation
        try:
            name = request.POST.get("name", "").strip()
            unit_name = request.POST.get("unit_name", "").strip().upper()
            total_supply = Decimal(request.POST.get("total_supply", "0"))
            decimals = int(request.POST.get("decimals", "6"))
            description = request.POST.get("description", "").strip()

            if not name or not unit_name:
                raise ValidationError(_("Name and unit name are required."))

            reserve_choice = request.POST.get("reserve_choice", "auto")
            if reserve_choice == "existing":
                reserve_pk = request.POST.get("reserve_account")
                reserve = get_object_or_404(LedgerAccount, pk=reserve_pk)
            else:
                reserve, _created = LedgerAccount.objects.get_or_create(
                    code=f"RES-{unit_name}",
                    defaults={
                        "name": f"{name} Reserve",
                        "account_type": AccountType.RESERVE,
                        "active": True,
                    },
                )

            from toto.mint.services import create_currency

            ref = f"mint-{unit_name.lower()}-{_uuid.uuid4().hex[:8]}"
            asset = create_currency(
                name=name,
                unit_name=unit_name,
                total_supply=total_supply,
                decimals=decimals,
                reserve_account=reserve,
                reference=ref,
                description=description,
            )
            messages.success(request, f"Asset {unit_name} minted with total supply of {total_supply}.")
            return redirect("assets:asset_detail", pk=asset.pk)
        except (ValidationError, InvalidOperation) as exc:
            messages.error(request, str(exc))

    return assets_render(request, "assets/asset_create.html", {
        "ledger_accounts": ledger_accounts,
    })


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
    context = {
        "asset": asset,
        "holders": holders,
        "recent_txs": recent_txs,
        "ledger_status": ledger_status,
        "asset_flow_url": flow_data_url,
        "asset_flow_full_url": flow_full_url,
    }
    from toto.assets.plugins.asset_plugins import AssetPlugin
    context["asset_plugin_sections"] = AssetPlugin.render_all(
        request=request,
        asset=asset,
        base_context=context,
    )
    # Distribution panel: staff or owner of the reserve account
    reserve = asset.reserve_account
    is_reserve_owner = (
        reserve and reserve.user_id and reserve.user_id == request.user.pk
    ) if request.user.is_authenticated else False
    can_distribute = request.user.is_staff or is_reserve_owner
    if can_distribute:
        context["can_distribute"] = True
        context["ledger_accounts"] = LedgerAccount.objects.filter(active=True).order_by("code")
    return assets_render(request, "assets/asset_detail.html", context)


@login_required
def asset_distribute(request, pk):
    """Transfer tokens from reserve to a recipient account.
    Allowed only for staff or the owner of the asset's reserve account."""
    from django.http import HttpResponseForbidden
    asset = get_object_or_404(Asset, pk=pk)
    reserve = asset.reserve_account
    is_reserve_owner = reserve and reserve.user_id and reserve.user_id == request.user.pk
    if not request.user.is_staff and not is_reserve_owner:
        return HttpResponseForbidden()
    if request.method == "POST":
        from decimal import InvalidOperation
        try:
            amount = Decimal(request.POST.get("amount", "0"))
            recipient_pk = request.POST.get("recipient_account")
            recipient = get_object_or_404(LedgerAccount, pk=recipient_pk)
            import uuid as _uuid
            ref = f"dist-{asset.unit_name.lower()}-{_uuid.uuid4().hex[:8]}"
            distribute_asset(asset=asset, recipient_account=recipient, amount=amount, reference=ref)
            messages.success(request, f"Distributed {amount} {asset.unit_name} to {recipient.code}.")
        except (ValidationError, Exception) as exc:
            messages.error(request, str(exc))
    return redirect("assets:asset_detail", pk=pk)


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
    from django.utils import timezone
    from toto.assets.wallet_pin import has_wallet_pin
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
        "now": timezone.now(),
        "account_flow_url": flow_data_url,
        "account_flow_full_url": flow_full_url,
        "has_wallet_pin": has_wallet_pin(request.user) if request.user.is_authenticated else False,
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

@login_required
def chain_verify(request):
    """Re-hash the whole ledger and report whether the chain still holds.

    Metered and priced because the cost is O(every transaction ever posted) —
    it re-reads each one's entries and re-computes its hash — and it grows
    monotonically. It was also reachable anonymously, which made it a free
    database-CPU amplifier.
    """
    from toto.quota import QuotaExceeded, check_quota, record_usage
    from toto.quota.charge import InsufficientFunds, charge, check_funds, price_for

    from .models import AssetsQuotaPolicy, AssetsUsageEvent

    tariff = price_for(request.user, "assets")
    try:
        check_quota(AssetsQuotaPolicy, "assets.chain.verify", 1, request.user)
        check_funds(request.user, tariff, "assets.chain.verify", 1)
    except (QuotaExceeded, InsufficientFunds) as exc:
        messages.error(request, str(exc))
        return redirect("assets:asset_list")

    valid = verify_hash_chain()
    from .models import LedgerHash
    hash_count = LedgerHash.objects.count()

    record_usage(AssetsQuotaPolicy.events, "assets.chain.verify", 1, request.user)
    charge(request.user, tariff, "assets.chain.verify", 1)

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


#: How far back the wallet's balance history looks.
BALANCE_HISTORY_DAYS = 30


def _balance_history_json(user, account_pks, *, days=BALANCE_HISTORY_DAYS) -> str:
    """Daily closing balance per asset, as a Chart.js line series.

    **Anchored on today's holding and walked BACKWARDS, not summed forwards
    from zero.** That is the whole design, and it is not the obvious choice, so:

    ``AssetHolding.balance_base_units`` is not a projection of the ledger. It is
    a cache, maintained by hand next to each entry write in four different
    modules, and there is no signal, no trigger and no recompute-from-entries
    anywhere. It can also be set directly in the Django admin with no entry
    written at all. So summing entries forwards from zero would produce a curve
    whose last point disagrees with the balance card printed directly above this
    chart — by a constant, forever, invisibly.

    Walking backwards from the holding makes the last point equal that card **by
    construction**. The history behind it is the best reconstruction the
    immutable entries allow, which is the right way round: the number somebody
    checks is exact, and the shape leading to it is honest about coming from
    movements.

    Scoped to ``account_pks`` — the same active accounts whose holdings the cards
    above are drawn from — so the chart and the cards cannot disagree about
    whose money this is.
    """
    import json
    from collections import defaultdict
    from datetime import timedelta

    from django.db.models import Sum
    from django.db.models.functions import TruncDate
    from django.utils import timezone

    from .models import from_base_units

    account_pks = list(account_pks)
    if not account_pks:
        return ""

    today = timezone.localdate()
    since = today - timedelta(days=days - 1)

    entries = LedgerEntry.objects.filter(account_id__in=account_pks)

    # Per (asset, day) movement over the window.
    #
    # The trailing .order_by("day") is load-bearing: LedgerEntry.Meta declares
    # ordering = ["created_at"], and Django appends an ORDER BY column to the
    # GROUP BY — which would silently return one row per ENTRY rather than one
    # per day, and every bucket would be a single movement.
    deltas: dict[int, dict] = defaultdict(dict)
    rows = (entries
            .filter(created_at__date__gte=since)
            .annotate(day=TruncDate("created_at"))
            .values("asset_id", "day")
            .annotate(total=Sum("amount_base_units"))
            .order_by("day"))
    for row in rows:
        deltas[row["asset_id"]][row["day"]] = int(row["total"] or 0)

    # The anchor: what each asset is worth right now, summed over those
    # accounts. Grouped by the ENTRY's asset elsewhere and by the HOLDING's
    # asset here — never by LedgerTransaction.asset, which is null on every
    # tariff charge and would drop all metered spending from the picture.
    anchors: dict[int, int] = defaultdict(int)
    holdings = (AssetHolding.objects
                .filter(account_id__in=account_pks)
                .select_related("asset"))
    assets = {}
    for holding in holdings:
        anchors[holding.asset_id] += holding.balance_base_units
        assets[holding.asset_id] = holding.asset

    # An asset spent down to nothing has movements and no holding row. Its line
    # belongs on the chart — "you had some and now you do not" is exactly what
    # somebody opens a history for.
    missing = set(deltas) - set(assets)
    if missing:
        for asset in Asset.objects.filter(pk__in=missing):
            assets[asset.pk] = asset

    if not assets:
        return ""

    labels = [(since + timedelta(days=i)).strftime("%m-%d") for i in range(days)]
    palette = ["#4f5fa1", "#5fa38c", "#d9a441", "#b45f8f", "#4a8f7a", "#c0603a"]

    datasets = []
    for index, asset_id in enumerate(sorted(assets, key=lambda pk: str(assets[pk]))):
        asset = assets[asset_id]
        by_day = deltas.get(asset_id, {})

        # Backwards from today: the balance at the end of a day, less what moved
        # during it, is the balance at the end of the day before.
        closing = [0] * days
        running = anchors.get(asset_id, 0)
        for offset in range(days - 1, -1, -1):
            closing[offset] = running
            running -= by_day.get(since + timedelta(days=offset), 0)

        # float, not Decimal: Django's JSON encoder writes a Decimal as a
        # STRING, and Chart.js will not plot a string. Kept in integer base
        # units until this last step so nothing rounds on the way.
        datasets.append({
            "label": asset.code or asset.name,
            "data": [float(from_base_units(units, asset.decimals))
                     for units in closing],
            "borderColor": palette[index % len(palette)],
            "backgroundColor": palette[index % len(palette)],
            "tension": 0.25,
            "pointRadius": 0,
            "borderWidth": 2,
        })

    # Only options.scales survives oya/partials/chart.html — it rebuilds
    # options.plugins wholesale, so a legend setting passed here would be
    # dropped without a word.
    return json.dumps({
        "chart_type": "line",
        "labels": labels,
        "datasets": datasets,
        "options": {"scales": {"y": {"beginAtZero": False}}},
    })


@login_required
def wallet(request):
    from django.utils import timezone
    from .models import AssetHolding
    accounts = (
        LedgerAccount.objects.filter(user=request.user, active=True)
        .order_by("-user_priority", "code")
        .prefetch_related('holdings__asset')
    )
    account_pks = list(accounts.values_list('pk', flat=True))
    recent_entries = (
        LedgerEntry.objects
        .filter(account__user=request.user)
        .select_related('transaction', 'asset', 'account')
        .order_by('-created_at')[:30]
    )
    # Assets carrying a display code — what the retired Currency table listed.
    currencies = Asset.objects.filter(active=True).exclude(code="")
    total_holdings = []
    for account in accounts:
        for h in account.holdings.all():
            if h.balance_base_units > 0:
                total_holdings.append(h)

    now = timezone.now()
    from toto.assets.wallet_pin import has_wallet_pin
    return assets_render(request, 'assets/wallet.html', {
        'wallet_accounts': accounts,
        'total_holdings': total_holdings,
        'recent_entries': recent_entries,
        'currencies': currencies,
        'now': now,
        'has_wallet_pin': has_wallet_pin(request.user),
        'balance_history_json': _balance_history_json(request.user, account_pks),
        'balance_history_days': BALANCE_HISTORY_DAYS,
    })


@require_POST
@login_required
def set_account_priority(request, pk):
    account = get_object_or_404(LedgerAccount, pk=pk, user=request.user, active=True)
    try:
        priority = int(request.POST.get("priority", 0))
    except (ValueError, TypeError):
        priority = 0
    account.user_priority = priority
    account.save(update_fields=["user_priority", "updated_at"])
    messages.success(request, f'Priority for "{account.name or account.code}" set to {priority}.')
    return redirect("assets:wallet")


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


@login_required
def wallet_pin_set(request):
    from toto.assets.wallet_pin import set_wallet_pin, has_wallet_pin
    has_pin = has_wallet_pin(request.user)
    if request.method == 'POST':
        pin = request.POST.get('pin', '').strip()
        confirm = request.POST.get('pin_confirm', '').strip()
        if not pin:
            messages.error(request, _('PIN cannot be empty.'))
        elif len(pin) < 4:
            messages.error(request, _('PIN must be at least 4 characters.'))
        elif pin != confirm:
            messages.error(request, _('PINs do not match.'))
        else:
            try:
                set_wallet_pin(request.user, pin)
                messages.success(request, _('Wallet PIN set successfully.'))
                return redirect('assets:wallet_pin_set')
            except Exception:
                messages.error(request, _('Could not save PIN. Please try again.'))
    return assets_render(request, 'assets/wallet_pin_set.html', {'has_pin': has_pin})


@login_required
def wallet_pin_verify(request):
    import json as _json
    from toto.assets.wallet_pin import check_wallet_pin, mark_session_verified
    if request.method != 'POST':
        return JsonResponse({'ok': False}, status=405)
    try:
        data = _json.loads(request.body)
        raw_pin = data.get('pin', '')
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Invalid request.'}, status=400)
    if not raw_pin:
        return JsonResponse({'ok': False, 'error': 'PIN is required.'})
    if check_wallet_pin(request.user, raw_pin):
        mark_session_verified(request.session)
        return JsonResponse({'ok': True})
    return JsonResponse({'ok': False, 'error': 'Incorrect PIN.'})


@login_required
def authorization_list(request):
    from .models import WalletAuthorization

    user_account_pks = list(
        LedgerAccount.objects.filter(user=request.user, active=True).values_list('pk', flat=True)
    )
    authorizations = list(
        WalletAuthorization.objects
        .filter(ledger_account__in=user_account_pks)
        .select_related('ledger_account')
        .order_by('-created_at')
    )

    if request.method == 'POST':
        action = request.POST.get('action')
        auth_pk = request.POST.get('auth_pk')
        if action == 'revoke' and auth_pk:
            try:
                auth = WalletAuthorization.objects.get(
                    pk=auth_pk, ledger_account__in=user_account_pks
                )
                auth.active = False
                auth.save(update_fields=['active', 'updated_at'])
                messages.success(request, f"Authorization '{auth.name}' revoked.")
            except WalletAuthorization.DoesNotExist:
                messages.error(request, _("Authorization not found."))
        return redirect('assets:authorization_list')

    return assets_render(request, 'assets/authorization_list.html', {
        'authorizations': authorizations,
    })


# --------------------------------------------------------------------------- #
# Attestation — what this platform says about its own books                     #
# --------------------------------------------------------------------------- #

def attestation(request):
    """Read-only, machine-readable, signed with this platform's key.

    Answers the master's audit and the top-up check with the SAME payload, so
    there is one format rather than two that must agree. Carries no user
    identities — account codes only, the privacy rule the clearing wire states
    for the same reason.

    Unauthenticated on purpose: it is signed, so it authenticates itself, and
    it reveals only aggregates a branch would tell its funder anyway. That
    also keeps toto-economy free of any dependency on toto-auth.
    """
    from django.http import JsonResponse

    from .statement import build_statement, sign_statement

    response = JsonResponse(sign_statement(build_statement()))
    response["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------------------
# The decorated ledger
# ---------------------------------------------------------------------------
#
# A page of its own rather than a section of `account_detail`, deliberately:
# account_detail carries no @login_required and no ownership check, so notes
# added there would be world-readable. Everything below proves ownership inside
# the lookup, the `set_account_priority` idiom — a wrong pk is a 404, not a 403.

def _my_account(request, pk):
    """The account, if it is the requester's (or they are staff)."""
    if request.user.is_staff:
        return get_object_or_404(LedgerAccount, pk=pk)
    return get_object_or_404(LedgerAccount, pk=pk, user=request.user)


@login_required
def account_ledger(request, pk):
    """One account's movements, with the notes kept beside them."""
    from . import decorations

    account = _my_account(request, pk)
    asset = None
    if request.GET.get("asset"):
        asset = Asset.objects.filter(unit_name=request.GET["asset"]).first()

    context = decorations.ledger_page_context(
        account,
        asset=asset,
        tag_slug=request.GET.get("tag", ""),
        page=request.GET.get("page") or 1,
        can_annotate=decorations.can_annotate_account(request.user, account),
    )
    context.update({
        # The partial reverses these, so the same markup serves the Business
        # Center's board-gated endpoints without this app knowing what a
        # company is.
        "annotate_key": account.pk,
        "comment_url_name": "assets:entry_comment",
        "tag_add_url_name": "assets:entry_tag_add",
        "tag_remove_url_name": "assets:entry_tag_remove",
    })
    return assets_render(request, "assets/account_ledger.html", context)


def _annotate_redirect(request, account):
    return redirect(f"{reverse('assets:account_ledger', args=[account.pk])}"
                    f"?{request.META.get('QUERY_STRING', '')}")


@require_POST
@login_required
def entry_comment(request, pk, entry_id):
    from . import decorations

    account = _my_account(request, pk)
    try:
        decorations.set_comment(account, entry_id, request.POST.get("body", ""),
                                user=request.user)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return _annotate_redirect(request, account)


@require_POST
@login_required
def entry_tag_add(request, pk, entry_id):
    from . import decorations

    account = _my_account(request, pk)
    try:
        decorations.add_tag(account, entry_id, request.POST.get("name", ""),
                            user=request.user)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return _annotate_redirect(request, account)


@require_POST
@login_required
def entry_tag_remove(request, pk, entry_id):
    from . import decorations

    account = _my_account(request, pk)
    try:
        decorations.remove_tag(account, entry_id, request.POST.get("tag") or 0)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return _annotate_redirect(request, account)
