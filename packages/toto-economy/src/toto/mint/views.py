"""The mint tab: engrave a currency, mint units, burn units.

Staff only, and only on a host that actually holds an issuer key — the second
check is not decoration, because a page can be reached by URL on any host that
somehow installed the app.

Three verbs are exposed here and they are three separate buttons on purpose.
ENGRAVE creates an identity and no units. MINT creates units under an identity
that already exists. BURN destroys units nobody holds. Collapsing them into one
"create money" action is exactly the confusion the redesign removed.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor

from .models import IssuanceRecord
from .services import burn, issue_asset, mint
from .summary import chain_summary, currency_rows, history


def _staff_only(request):
    # PermissionDenied, never a redirect: a 302 to the login page turns a
    # refusal into an HTML 200 for anything polling this.
    if not (request.user.is_staff or request.user.is_superuser):
        raise PermissionDenied("The mint is staff only.")


def _master_only(request):
    from toto.assets.issuer import is_monetary_master

    if not is_monetary_master():
        raise PermissionDenied(
            "This platform holds no monetary issuer key, so it cannot engrave, "
            "mint or burn anything.")


@login_required
def index(request):
    _staff_only(request)
    from toto.assets.issuer import is_monetary_master, local_issuer

    context = {
        "records": IssuanceRecord.objects.select_related("asset", "actor")[:50],
        "is_master": is_monetary_master(),
        "issuer": local_issuer(),
        "currencies": currency_rows(),
        "chain": chain_summary(),
        "events": history(),
    }
    return render(request, "mint/index.html",
                  PageProcessor().decorate(context, request))


def _amount(request, field="amount"):
    raw = (request.POST.get(field, "") or "").strip()
    try:
        return Decimal(raw or "0")
    except InvalidOperation:
        raise ValidationError(f"“{raw}” is not a number.")


def _report(request, exc):
    if isinstance(exc, ValidationError):
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.error(request, str(exc))
    return redirect("mint:index")


@login_required
@require_POST
def issue(request):
    """ENGRAVE a currency and MINT its opening supply."""
    _staff_only(request)
    _master_only(request)
    try:
        asset = issue_asset(
            name=request.POST.get("name", "").strip(),
            unit_name=request.POST.get("unit_name", "").strip().upper(),
            total_supply=_amount(request, "total_supply"),
            decimals=int(request.POST.get("decimals", "0") or 0),
            code=request.POST.get("code", "").strip().upper(),
            symbol=request.POST.get("symbol", "").strip(),
            reason=request.POST.get("reason", ""),
            actor=request.user)
    except Exception as exc:  # noqa: BLE001 - shown to staff, never a 500
        return _report(request, exc)

    messages.success(
        request,
        f"Engraved {asset.unit_name} with a maximum of "
        f"{asset.max_supply_display} and minted all of it into the reserve. "
        f"Identity {asset.currency_hash[:20]}…")
    return redirect("mint:index")


@login_required
@require_POST
def mint_units(request, pk):
    """MINT: more units of a currency that already exists."""
    from toto.assets.models import Asset

    _staff_only(request)
    _master_only(request)
    asset = get_object_or_404(Asset, pk=pk, is_mirror=False)
    try:
        event = mint(asset=asset, amount=_amount(request),
                     reason=request.POST.get("reason", ""), actor=request.user)
    except Exception as exc:  # noqa: BLE001 - shown to staff, never a 500
        return _report(request, exc)

    messages.success(
        request,
        f"Minted {event.amount_base_units} base units of {asset.unit_name} "
        f"into its reserve. Event #{event.sequence}, "
        f"{event.event_hash[:20]}…")
    return redirect("mint:index")


@login_required
@require_POST
def burn_units(request, pk):
    """BURN: destroy units held in the reserve."""
    from toto.assets.models import Asset

    _staff_only(request)
    _master_only(request)
    asset = get_object_or_404(Asset, pk=pk, is_mirror=False)
    try:
        event = burn(asset=asset, amount=_amount(request),
                     reason=request.POST.get("reason", ""), actor=request.user)
    except Exception as exc:  # noqa: BLE001 - shown to staff, never a 500
        return _report(request, exc)

    messages.success(
        request,
        f"Burned {event.amount_base_units} base units of {asset.unit_name} "
        f"from its reserve. Event #{event.sequence}, "
        f"{event.event_hash[:20]}…")
    return redirect("mint:index")
