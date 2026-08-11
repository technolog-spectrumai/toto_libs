"""The mint tab: create a new fixed-supply asset.

Staff only, and only on a host that actually holds an issuer key — the second
check is not decoration, because a page can be reached by URL on any host that
somehow installed the app.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor

from .models import IssuanceRecord
from .services import issue_asset


def _staff_only(request):
    # PermissionDenied, never a redirect: a 302 to the login page turns a
    # refusal into an HTML 200 for anything polling this.
    if not (request.user.is_staff or request.user.is_superuser):
        raise PermissionDenied("The mint is staff only.")


@login_required
def index(request):
    _staff_only(request)
    from toto.assets.issuer import is_monetary_master, local_issuer

    context = {
        "records": IssuanceRecord.objects.select_related("asset", "actor")[:50],
        "is_master": is_monetary_master(),
        "issuer": local_issuer(),
    }
    return render(request, "mint/index.html",
                  PageProcessor().decorate(context, request))


@login_required
@require_POST
def issue(request):
    _staff_only(request)
    try:
        supply = Decimal(request.POST.get("total_supply", "").strip() or "0")
    except InvalidOperation:
        messages.error(request, "That total supply is not a number.")
        return redirect("mint:index")

    try:
        asset = issue_asset(
            name=request.POST.get("name", "").strip(),
            unit_name=request.POST.get("unit_name", "").strip().upper(),
            total_supply=supply,
            decimals=int(request.POST.get("decimals", "0") or 0),
            code=request.POST.get("code", "").strip().upper(),
            symbol=request.POST.get("symbol", "").strip(),
            reason=request.POST.get("reason", ""),
            actor=request.user)
    except (ValidationError, Exception) as exc:  # noqa: BLE001 - shown to staff
        if isinstance(exc, ValidationError):
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.error(request, str(exc))
        return redirect("mint:index")

    messages.success(
        request,
        f"Issued {asset.unit_name} — {asset.max_supply_display} into its "
        f"reserve. Identity {asset.currency_hash[:20]}…")
    return redirect("mint:index")
