"""Setting and reading time grants — the one door every Time dial posts to.

Validation, ownership and billing attribution live here so the per-app cards
(ambrosia room, manta tab, fileservices list) and the central Demurrage tab
all behave identically. Rules:

* a value must sit between the declaration's free default and its ceiling;
* a workspace-scoped dial may only be set by the workspace's owner, and the
  grant bills that owner (denormalized at write time);
* setting a dial back to its free default DELETES the grant — a grant at the
  default is dead weight that would clutter the Demurrage list and the levy.
"""

from __future__ import annotations

from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404

from toto.quota import rates
from toto.quota.times import registry as time_registry

from .models import TimeGrant


def _declaration(key: str):
    decl = time_registry.get(key)
    if decl is None:
        raise ValidationError(f"No time dial is declared as {key!r}.")
    return decl


def _resolve_scope(decl, actor, scope_id):
    """The (billing_user, scope_id) pair after ownership checks."""
    if decl.scope == "workspace":
        if scope_id is None:
            raise ValidationError(f"{decl.key} needs the workspace it applies to.")
        try:
            model = apps.get_model(decl.scope_model)
        except LookupError:
            raise Http404(f"{decl.scope_model} is not installed on this host.")
        obj = model.objects.filter(pk=scope_id).first()
        if obj is None:
            raise Http404("That workspace no longer exists.")
        owner_id = getattr(obj, decl.scope_owner_attr)
        if owner_id != actor.pk:
            raise PermissionDenied("Only the owner may set this dial.")
        return obj.owner if hasattr(obj, "owner") else actor, scope_id
    return actor, None


def set_grant(*, actor, key: str, seconds: int, scope_id=None) -> TimeGrant | None:
    """Set one dial. Returns the grant, or None when it was reset to free."""
    decl = _declaration(key)
    billing_user, scope_id = _resolve_scope(decl, actor, scope_id)

    seconds = int(seconds)
    if seconds < decl.free_seconds or seconds > decl.ceiling_seconds:
        raise ValidationError(
            f"{decl.label} must be between {decl.free_seconds} and "
            f"{decl.ceiling_seconds} seconds."
        )

    lookup = {"key": key}
    lookup["scope_id"] = scope_id if scope_id is not None else None
    if scope_id is None:
        lookup["user"] = billing_user

    if seconds == decl.free_seconds:
        TimeGrant.objects.filter(**lookup).delete()
        return None

    grant = TimeGrant.objects.filter(**lookup).first()
    if grant is None:
        grant = TimeGrant(key=key, scope_id=scope_id)
    grant.user = billing_user
    grant.seconds = seconds
    grant.save()
    return grant


def clear_grant(*, actor, key: str, scope_id=None) -> None:
    decl = _declaration(key)
    set_grant(actor=actor, key=key, seconds=decl.free_seconds, scope_id=scope_id)


def rows_for_user(user) -> dict:
    """The Demurrage tab's data: every grant, priced, plus totals."""
    price = rates.rate_card().get("time.hold")
    rows = []
    total_extra = 0
    for grant in TimeGrant.objects.filter(user=user).order_by("key", "scope_id"):
        decl = time_registry.get(grant.key)
        if decl is None:
            continue
        extra = max(0, min(grant.seconds, decl.ceiling_seconds) - decl.free_seconds)
        total_extra += extra
        rows.append({
            "grant": grant,
            "decl": decl,
            "scope_label": _scope_label(decl, grant),
            "extra_seconds": extra,
            "extra_hours": round(extra / 3600, 4),
            "estimate": _estimate(price, extra),
        })
    # The levy bills only the excess above the time.hold rule's allowance
    # (seeded 0, but a staff edit must not make this page overstate the bill).
    # Per-row estimates stay marginal — correct once the allowance is spent.
    allowance_seconds = 0
    from .models import TaxRule

    hold_rule = TaxRule.objects.filter(metric_code="time.hold").first()
    if hold_rule is not None:
        allowance_seconds = int(hold_rule.allowance * 3600)
    billable_extra = max(0, total_extra - allowance_seconds)
    return {
        "rows": rows,
        "price": price,
        "total_extra_hours": round(total_extra / 3600, 4),
        "total_estimate": _estimate(price, billable_extra),
    }


def _scope_label(decl, grant) -> str:
    if grant.scope_id is None or not decl.scope_model:
        return ""
    try:
        model = apps.get_model(decl.scope_model)
    except LookupError:
        return f"#{grant.scope_id}"
    obj = model.objects.filter(pk=grant.scope_id).first()
    if obj is None:
        return "(deleted workspace)"
    return getattr(obj, "name", None) or str(obj)


def _estimate(price, extra_seconds: int):
    if price is None or extra_seconds <= 0:
        return None
    from decimal import Decimal

    hours = Decimal(extra_seconds) / Decimal(3600)
    unit_quantity = price["unit_quantity"] or Decimal("1")
    amount = hours * Decimal(price["price_display"]) / Decimal(unit_quantity)
    return {
        "amount": amount.quantize(Decimal(10) ** -price["asset_decimals"]),
        "asset": price["asset"],
    }
