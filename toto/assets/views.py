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

from .forms import AgreementForm, ContractForm, TokenizationCreateForm, TokenizationDefaultForm
from .hashing import verify_hash_chain
from .models import (
    AccountType,
    Asset,
    AssetHolding,
    LedgerAccount,
    LedgerEntry,
    LedgerTransaction,
    Tokenization,
    TokenizationStatus,
    TransactionType,
)
from .queries import list_asset_holders, verify_asset_ledger
from .services.assets import create_asset, default_tokenization, distribute_asset


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
                raise ValidationError("Name and unit name are required.")

            reserve_choice = request.POST.get("reserve_choice", "auto")
            if reserve_choice == "existing":
                reserve_pk = request.POST.get("reserve_account")
                reserve = get_object_or_404(LedgerAccount, pk=reserve_pk)
            else:
                reserve, _ = LedgerAccount.objects.get_or_create(
                    code=f"RES-{unit_name}",
                    defaults={
                        "name": f"{name} Reserve",
                        "account_type": AccountType.RESERVE,
                        "active": True,
                    },
                )

            ref = f"mint-{unit_name.lower()}-{_uuid.uuid4().hex[:8]}"
            asset = create_asset(
                name=name,
                unit_name=unit_name,
                total_supply=total_supply,
                decimals=decimals,
                reserve_account=reserve,
                reference=ref,
                description=description,
            )
            asset.reserve_account = reserve
            asset.save(update_fields=["reserve_account", "updated_at"])
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


@login_required
def tokenization_create_for_object(request, object_id):
    from toto.inventory.models import RealWorldObject

    obj = get_object_or_404(
        RealWorldObject.objects.select_related("object_type", "owner", "custodian"),
        id=object_id,
    )
    existing = obj.tokenizations.select_related("asset").first()
    if existing:
        messages.info(request, "This object is already tokenized and cannot be tokenized again.")
        return redirect("assets:asset_detail", pk=existing.asset_id)

    if request.method == "POST":
        form = TokenizationCreateForm(request.POST, real_world_object=obj)
        if form.is_valid():
            data = form.cleaned_data
            metadata = data.get("metadata") or {}
            try:
                with transaction.atomic():
                    asset = create_asset(
                        name=data["asset_name"],
                        unit_name=data["unit_name"],
                        total_supply=data["total_supply"],
                        decimals=data["decimals"],
                        reserve_account=data["reserve_account"],
                        reference=f"tokenize-{obj.pk}-{data['unit_name'].lower()}",
                        description=f"Tokenization asset for inventory object {obj.name}",
                        metadata={
                            **metadata,
                            "tokenized_object_id": obj.pk,
                            "tokenized_object_name": obj.name,
                        },
                    )
                    asset.is_currency = data.get("is_currency", False)
                    asset.backing_document = data.get("backing_document", "")
                    asset.minting_authority = data.get("minting_authority", "")
                    asset.save(update_fields=["is_currency", "backing_document", "minting_authority", "updated_at"])
                    Tokenization.objects.create(
                        real_world_object=obj,
                        asset=asset,
                        supervisor=data.get("supervisor"),
                        metadata=metadata,
                    )
            except ValidationError as exc:
                form.add_error(None, exc.messages[0] if hasattr(exc, "messages") else str(exc))
            else:
                messages.success(request, f"{obj.name} tokenized as {asset.unit_name}.")
                return redirect("assets:asset_detail", pk=asset.pk)
    else:
        initial_name = f"{obj.name} Token"
        form = TokenizationCreateForm(
            real_world_object=obj,
            initial={
                "asset_name": initial_name,
                "unit_name": "".join(ch for ch in obj.name.upper() if ch.isalnum())[:12] or f"OBJ{obj.pk}",
                "decimals": 0,
                "total_supply": obj.quantity or 1,
                "backing_document": obj.description,
            },
        )

    return assets_render(request, "assets/tokenization_form.html", {
        "form": form,
        "object": obj,
    })


@require_POST
@login_required
def tokenization_default(request, pk):
    tokenization = get_object_or_404(
        Tokenization.objects.select_related("asset", "real_world_object"),
        pk=pk,
    )
    next_url = request.POST.get("next") or reverse("assets:asset_detail", kwargs={"pk": tokenization.asset_id})
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = reverse("assets:asset_detail", kwargs={"pk": tokenization.asset_id})

    if tokenization.status == TokenizationStatus.DEFAULTED:
        messages.info(request, "This tokenization has already defaulted.")
        return redirect(next_url)

    form = TokenizationDefaultForm(request.POST)
    if form.is_valid():
        data = form.cleaned_data
        try:
            defaulted = default_tokenization(
                tokenization=tokenization,
                reason=data["reason"],
                note=data.get("note", ""),
                defaulted_by=request.user,
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(
                request,
                f"{defaulted.asset.unit_name} defaulted; the asset is now inactive.",
            )
    else:
        messages.error(request, "Choose a valid default reason.")
    return redirect(next_url)


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
    from .models import Obligation, ObligationStatus
    from toto.bazaar.wallet_pin import has_wallet_pin
    account = get_object_or_404(LedgerAccount, pk=pk)
    holdings = AssetHolding.objects.filter(account=account).select_related("asset")
    recent_entries = (
        LedgerEntry.objects.filter(account=account)
        .select_related("transaction", "asset")
        .order_by("-created_at")[:30]
    )
    obligations = (
        Obligation.objects
        .filter(debtor_account=account, status=ObligationStatus.PENDING)
        .select_related('creditor_account', 'asset')
        .order_by('due_at')
    )
    flow_data_url = reverse("assets:ledger_flow_data") + f"?account={account.code}"
    flow_full_url = reverse("assets:ledger_flow") + f"?account={account.code}"
    return assets_render(request, "assets/account_detail.html", {
        "account": account,
        "holdings": holdings,
        "recent_entries": recent_entries,
        "obligations": obligations,
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


@login_required
def wallet(request):
    from django.utils import timezone
    from .models import Currency, AssetHolding, Obligation, ObligationStatus
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
    currencies = Currency.objects.filter(is_active=True).select_related('asset')
    total_holdings = []
    for account in accounts:
        for h in account.holdings.all():
            if h.balance_base_units > 0:
                total_holdings.append(h)

    now = timezone.now()
    obligations = (
        Obligation.objects
        .filter(debtor_account__in=account_pks, status=ObligationStatus.PENDING)
        .select_related('debtor_account', 'creditor_account', 'asset')
        .order_by('due_at')
    )
    # Mark overdue in Python so template can use is_overdue property
    from toto.bazaar.wallet_pin import has_wallet_pin
    return assets_render(request, 'assets/wallet.html', {
        'wallet_accounts': accounts,
        'total_holdings': total_holdings,
        'recent_entries': recent_entries,
        'currencies': currencies,
        'obligations': obligations,
        'now': now,
        'has_wallet_pin': has_wallet_pin(request.user),
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


@require_POST
@login_required
def obligation_fulfill(request, pk):
    from .models import Obligation, ObligationStatus
    from .services.assets import fulfill_obligation
    from toto.bazaar.wallet_pin import has_wallet_pin, session_is_verified

    obligation = get_object_or_404(
        Obligation.objects.select_related('debtor_account', 'creditor_account', 'asset'),
        pk=pk,
        status=ObligationStatus.PENDING,
    )

    if obligation.debtor_account.user_id != request.user.id:
        messages.error(request, _("You can only fulfill obligations from your own wallet."))
        return redirect('assets:wallet')

    if not has_wallet_pin(request.user):
        messages.error(request, _("Set a wallet PIN before fulfilling obligations."))
        return redirect('assets:wallet_pin_set')

    next_url = request.POST.get("next") or reverse("assets:wallet")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = reverse("assets:wallet")

    if not session_is_verified(request.session):
        messages.error(request, _("Verify your wallet PIN before fulfilling obligations."))
        return redirect(next_url)

    try:
        tx = fulfill_obligation(
            obligation=obligation,
            reference=f"fulfill-obligation-{obligation.pk}",
            description=f"Fulfilling obligation {obligation.reference}",
        )
        messages.success(
            request,
            _("Obligation fulfilled. Transfer %(reference)s posted.") % {"reference": tx.reference},
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))

    return redirect(next_url)


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
    from toto.bazaar.wallet_pin import set_wallet_pin, has_wallet_pin
    has_pin = has_wallet_pin(request.user)
    if request.method == 'POST':
        pin = request.POST.get('pin', '').strip()
        confirm = request.POST.get('pin_confirm', '').strip()
        if not pin:
            messages.error(request, 'PIN cannot be empty.')
        elif len(pin) < 4:
            messages.error(request, 'PIN must be at least 4 characters.')
        elif pin != confirm:
            messages.error(request, 'PINs do not match.')
        else:
            try:
                set_wallet_pin(request.user, pin)
                messages.success(request, 'Wallet PIN set successfully.')
                return redirect('assets:wallet_pin_set')
            except Exception:
                messages.error(request, 'Could not save PIN. Please try again.')
    return assets_render(request, 'assets/wallet_pin_set.html', {'has_pin': has_pin})


@login_required
def agreement_list(request):
    from .models import Agreement
    agreements = Agreement.objects.select_related("source_account", "target_account", "contract").order_by("-created_at")
    return assets_render(request, "assets/agreement_list.html", {"agreements": agreements})


@login_required
def agreement_detail(request, pk):
    from .models import Agreement
    from .lapis.loader import loads_contract
    from .lapis.compiler import ContractFlowValidator
    agreement = get_object_or_404(
        Agreement.objects.select_related("source_account", "target_account", "contract"),
        pk=pk,
    )
    flow_steps = []
    if agreement.contract and agreement.contract.code:
        try:
            tree = loads_contract(agreement.contract.code, fmt="yaml")
            flow_steps = ContractFlowValidator().get_steps(tree)
        except Exception:
            pass
    return assets_render(request, "assets/agreement_detail.html", {
        "agreement": agreement,
        "flow_steps": flow_steps,
    })


@login_required
def agreement_create(request):
    import json as _json
    from .models import Contract
    if request.method == "POST":
        form = AgreementForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    code = form.cleaned_data.get("code", "").strip()
                    contract = form.cleaned_data.get("contract")
                    if code and not contract:
                        name = f"Inline contract for {form.cleaned_data['source_account'].code}→{form.cleaned_data['target_account'].code}"
                        contract = Contract.objects.create(name=name, code=code)
                    agreement = form.save(commit=False)
                    agreement.contract = contract
                    agreement.save()
                messages.success(request, f"Agreement {agreement.uuid} created.")
                return redirect("assets:agreement_detail", pk=agreement.pk)
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
    else:
        form = AgreementForm()
    contracts_data = {
        str(c.pk): {"name": c.name, "code": c.code}
        for c in Contract.objects.all()
    }
    return assets_render(request, "assets/agreement_form.html", {
        "form": form,
        "contract_codes_json": _json.dumps(contracts_data),
    })




@login_required
def contract_list(request):
    from .models import Contract
    contracts = Contract.objects.order_by("name")
    return assets_render(request, "assets/contract_list.html", {"contracts": contracts})


@login_required
def contract_detail(request, uuid):
    from .models import Contract
    from .lapis.loader import loads_contract
    from .lapis.compiler import ContractFlowValidator
    contract = get_object_or_404(Contract, uuid=uuid)
    flow_steps = []
    if contract.code:
        try:
            tree = loads_contract(contract.code, fmt="yaml")
            flow_steps = ContractFlowValidator().get_steps(tree)
        except Exception:
            pass
    try:
        instrument = contract.financial_instrument
    except Exception:
        instrument = None
    return assets_render(request, "assets/contract_detail.html", {
        "contract": contract,
        "flow_steps": flow_steps,
        "instrument": instrument,
    })


@login_required
def contract_create(request):
    if request.method == "POST":
        form = ContractForm(request.POST)
        if form.is_valid():
            contract = form.save()
            messages.success(request, f"Contract '{contract.name}' created.")
            return redirect("assets:contract_detail", uuid=contract.uuid)
    else:
        form = ContractForm()
    return assets_render(request, "assets/contract_form.html", {"form": form, "is_create": True})


@login_required
def contract_update(request, uuid):
    from .models import Contract
    contract = get_object_or_404(Contract, uuid=uuid)
    if request.method == "POST":
        form = ContractForm(request.POST, instance=contract)
        if form.is_valid():
            form.save()
            messages.success(request, f"Contract '{contract.name}' updated.")
            return redirect("assets:contract_detail", uuid=contract.uuid)
    else:
        form = ContractForm(instance=contract)
    return assets_render(request, "assets/contract_form.html", {"form": form, "contract": contract, "is_create": False})




@login_required
def contract_cytoscape_json(request, uuid):
    from .models import Contract
    from .lapis.loader import loads_contract
    from .lapis.cytoscape import flow_to_cytoscape
    contract = get_object_or_404(Contract, uuid=uuid)
    if not contract.code:
        return JsonResponse({"nodes": [], "edges": []})
    try:
        tree = loads_contract(contract.code, fmt="yaml")
        data = flow_to_cytoscape(tree)
    except Exception as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse(data)


@login_required
def wallet_pin_verify(request):
    import json as _json
    from toto.bazaar.wallet_pin import check_wallet_pin, mark_session_verified
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
                messages.error(request, "Authorization not found.")
        return redirect('assets:authorization_list')

    return assets_render(request, 'assets/authorization_list.html', {
        'authorizations': authorizations,
    })
