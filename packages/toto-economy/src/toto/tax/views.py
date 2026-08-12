"""The two levy screens: yours, and the staff desk for allowances.

Prices are deliberately not editable here — the rate desk at /quota/rates/ is
the one place a number becomes a charge, and this page links to it. What the
desk cannot express is the allowance, which is this app's own knob.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from toto.quota import rates
from toto.quota.levy import registry as levy_registry
from toto.quota.metrics import registry as metric_registry
from toto.ui import PageProcessor

from . import services
from .models import TaxRule


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _staff_only(request):
    if not request.user.is_staff:
        raise PermissionDenied


@login_required
def my_levies(request):
    """What you hold, what of it is free, and what tonight's levy will cost."""
    from . import surplus

    return _render(request, "tax/my_levies.html", {
        "rows": services.estimate_for_user(request.user),
        "community_rows": surplus.estimate_for_user(request.user),
        "wallet_url": rates.wallet_url(),
        "balance": rates.balance_of(request.user),
    })


@login_required
def demurrage(request):
    """Every raised time dial you hold, what it costs per day, and the edits.

    The central editor — the per-app Time cards are thin because full editing
    lives here, and every card posts to the same door (time_grant_set)."""
    from . import services, timegrants
    from .models import ArrearsStatus, TaxArrearsCase, TaxRule

    data = timegrants.rows_for_user(request.user)
    hold_rule = TaxRule.objects.filter(metric_code="time.hold").first()
    case = None
    if hold_rule is not None:
        case = (TaxArrearsCase.objects
                .filter(user=request.user, rule=hold_rule,
                        status__in=[ArrearsStatus.OPEN, ArrearsStatus.WARNED])
                .first())
    return _render(request, "tax/demurrage.html", {
        **data,
        "case": case,
        "wallet_url": rates.wallet_url(),
        "balance": rates.balance_of(request.user),
    })


@login_required
@require_POST
def time_grant_set(request):
    """The one POST door for every Time dial platform-wide."""
    from django.http import Http404

    from . import timegrants

    key = request.POST.get("key", "")
    raw_scope = (request.POST.get("scope_id") or "").strip()
    scope_id = int(raw_scope) if raw_scope.isdigit() else None
    raw_seconds = (request.POST.get("seconds") or "").strip()

    try:
        if raw_seconds == "":
            timegrants.clear_grant(actor=request.user, key=key, scope_id=scope_id)
            messages.success(request, _("Reset to the free default."))
        else:
            grant = timegrants.set_grant(
                actor=request.user, key=key,
                seconds=int(raw_seconds), scope_id=scope_id)
            if grant is None:
                messages.success(request, _("Reset to the free default."))
            else:
                messages.success(request, _("Time limit saved."))
    except (ValueError, ValidationError) as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", None) or [str(exc)]))
    except Http404:
        messages.error(request, _("That workspace no longer exists."))

    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()},
            require_https=request.is_secure()):
        return redirect(next_url)
    return redirect("tax:demurrage")


@login_required
def rules(request):
    """One row per registered levy provider: its rule, allowance and price."""
    _staff_only(request)

    if request.method == "POST":
        plan, errors = _parse_rules(request.POST)
        surplus_plan, surplus_errors = _parse_surplus(request.POST)
        if errors or surplus_errors:
            for message in [*errors, *surplus_errors]:
                messages.error(request, message)
        else:
            with transaction.atomic():
                _apply_rules(plan)
                _apply_surplus(surplus_plan)
            messages.success(request, _("Allowances saved."))
            return redirect("tax:rules")

    card = rates.rate_card()
    rules_by_code = {rule.metric_code: rule for rule in TaxRule.objects.all()}
    rows = []
    for provider in levy_registry.all():
        metric = metric_registry.get(provider.metric_code)
        rows.append({
            "provider": provider,
            "metric": metric,
            "rule": rules_by_code.get(provider.metric_code),
            "price": card.get(provider.metric_code),
        })
    return _render(request, "tax/rules.html", {
        "rows": rows,
        "rate_desk_url": reverse("quota:rate_desk"),
        "price_asset": rates.price_asset_symbol(),
        "surplus_rows": _surplus_rows(),
        "surplus_periods": _surplus_period_choices(),
    })


def _surplus_period_choices():
    from .models import SurplusPeriod

    return SurplusPeriod.choices


def _surplus_rows():
    """One row per active asset — the tax is on holdings, so every asset is
    armable; staff choosing which to arm IS the filter."""
    from toto.assets.models import Asset

    from .models import SurplusPolicy

    policies = {p.asset_id: p for p in SurplusPolicy.objects.all()}
    rows = []
    for asset in Asset.objects.filter(active=True).order_by("unit_name"):
        policy = policies.get(asset.pk)
        from .surplus import rate_pct_text

        rows.append({
            "asset": asset,
            "policy": policy,
            # The grid speaks PERCENT; the model stores the fraction.
            "rate_pct": rate_pct_text(policy.rate) if policy else "",
        })
    return rows


def _parse_rules(post):
    """Read the whole grid before writing any of it — the rate-desk shape."""
    plan, errors = [], []
    for provider in levy_registry.all():
        code = provider.metric_code
        key = f"allowance__{code}"
        if key not in post:
            continue
        raw = (post.get(key) or "").strip()
        try:
            allowance = Decimal(raw) if raw != "" else Decimal("0")
        except (InvalidOperation, ValueError):
            errors.append(_("%(code)s: %(value)r is not a number.")
                          % {"code": code, "value": raw})
            continue
        if allowance < 0:
            errors.append(_("%(code)s: an allowance cannot be negative.") % {"code": code})
            continue
        plan.append({
            "code": code,
            "allowance": allowance,
            "active": bool(post.get(f"active__{code}")),
        })
    return plan, errors


def _apply_rules(plan):
    """Write a validated plan. Caller owns the transaction."""
    for entry in plan:
        rule = TaxRule.objects.filter(metric_code=entry["code"]).first()
        if rule is None:
            rule = TaxRule(metric_code=entry["code"])
        rule.allowance = entry["allowance"]
        rule.active = entry["active"]
        rule.save()


def _parse_surplus(post):
    """The community-fee grid: read the whole thing before writing any of it.
    Key absent = row not rendered, leave alone; blank threshold = delete the
    policy; valued = upsert. Rates are typed as percent, stored as fraction."""
    from .models import SurplusPeriod

    plan, errors = [], []
    from toto.assets.models import Asset

    for asset in Asset.objects.filter(active=True):
        key = f"threshold__{asset.pk}"
        if key not in post:
            continue
        raw_threshold = (post.get(key) or "").strip()
        if raw_threshold == "":
            plan.append({"asset": asset, "delete": True})
            continue
        raw_rate = (post.get(f"rate_pct__{asset.pk}") or "").strip()
        period = (post.get(f"period__{asset.pk}") or "").strip()
        try:
            threshold = Decimal(raw_threshold)
            rate_pct = Decimal(raw_rate or "0")
        except (InvalidOperation, ValueError):
            errors.append(_("%(asset)s: the threshold and rate must be numbers.")
                          % {"asset": asset.unit_name})
            continue
        if threshold < 0:
            errors.append(_("%(asset)s: the threshold cannot be negative.")
                          % {"asset": asset.unit_name})
            continue
        if not (Decimal("0") <= rate_pct <= Decimal("25")):
            errors.append(_("%(asset)s: the rate must be between 0 and 25 percent.")
                          % {"asset": asset.unit_name})
            continue
        if period not in SurplusPeriod.values:
            errors.append(_("%(asset)s: pick a period.") % {"asset": asset.unit_name})
            continue
        plan.append({
            "asset": asset, "delete": False,
            "threshold": threshold,
            "rate": rate_pct / Decimal("100"),
            "period": period,
            "active": bool(post.get(f"surplus_active__{asset.pk}")),
        })
    return plan, errors


def _apply_surplus(plan):
    """Write a validated community-fee plan. Caller owns the transaction."""
    from .models import SurplusPolicy

    for entry in plan:
        if entry["delete"]:
            SurplusPolicy.objects.filter(asset=entry["asset"]).delete()
            continue
        policy = SurplusPolicy.objects.filter(asset=entry["asset"]).first()
        if policy is None:
            policy = SurplusPolicy(asset=entry["asset"])
        policy.threshold_display = entry["threshold"]
        policy.rate = entry["rate"]
        policy.period = entry["period"]
        policy.active = entry["active"]
        policy.save()
