"""What is left of this app's own screens: the one door every time dial writes
through, and the redirects that keep old links alive.

There were three pages here — my levies, the allowances desk, and the demurrage
tab — and all three were organised by MODEL rather than by the thing being
metered. A levy needed two of them plus a third in another app to configure, and
none was sufficient alone: the allowance lived here, the price on the rate desk,
and filling in only one produced a levy that looked armed and charged nothing.

Every one of those knobs now sits on the metered thing it belongs to, at
``quota:metric_detail``. The dial roll-up is a section of ``time.hold``; a
levy's allowance is a field on the levied thing. What could not move is
:func:`time_grant_set`: it is the single POST target for every Time card in
every app, so it stays exactly where it was, at the URL they already post to.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST


def _staff_only(request):
    if not request.user.is_staff:
        raise PermissionDenied


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
    # The dial roll-up lives on the thing that bills held time.
    return redirect("quota:metric_detail", code="time.hold")


# ---------------------------------------------------------------------------
# Retired pages
# ---------------------------------------------------------------------------
# my_levies, demurrage and rules are gone as destinations, not as features. Each
# was one model's CRUD screen; what they edited now lives on the metered thing.
# These redirects exist because a live host has them bookmarked, they are named
# in the manual, and `next=` on a dial form could still carry one.


@login_required
def my_levies(request):
    """Superseded by the metered things themselves — every levy is one of them."""
    return redirect("quota:index")


@login_required
def demurrage(request):
    """Superseded by the ``time.hold`` thing, which carries the dial roll-up."""
    return redirect("quota:metric_detail", code="time.hold")


@login_required
def rules(request):
    """Superseded: an allowance is a field on the thing it makes free."""
    _staff_only(request)
    return redirect("quota:index")


@login_required
@require_POST
def holding_fee_set(request, asset_id: int):
    """Set (or clear) one asset's holding fee.

    A holding fee is a property of an ASSET — a share of what you hold above a
    threshold — so it is edited on the asset, beside everything else that is
    true of that asset. It used to be the bottom half of a page called
    "Allowances" whose top half configured something else entirely: the free
    allowances of recurring levies, which are keyed by metric and now live on
    the metered things themselves. Two grids, two objects, one screen, one save
    button; splitting them is the point.

    Blank threshold deletes the policy — the rule the grid already used, kept so
    an operator's muscle memory survives the move.
    """
    from decimal import Decimal, InvalidOperation

    from django.shortcuts import get_object_or_404

    from toto.assets.models import Asset

    from .models import SurplusPeriod, SurplusPolicy

    _staff_only(request)
    asset = get_object_or_404(Asset, pk=asset_id)
    back = redirect("assets:asset_detail", pk=asset.pk)

    raw_threshold = (request.POST.get("threshold") or "").strip()
    if raw_threshold == "":
        SurplusPolicy.objects.filter(asset=asset).delete()
        messages.success(request, _("Holding fee removed."))
        return back

    try:
        threshold = Decimal(raw_threshold)
        rate_pct = Decimal((request.POST.get("rate_pct") or "0").strip() or "0")
    except (InvalidOperation, ValueError):
        messages.error(request, _("The threshold and rate must be numbers."))
        return back
    if threshold < 0:
        messages.error(request, _("The threshold cannot be negative."))
        return back
    # Percent in the UI, fraction in the database — one conversion, in one
    # place. The model's own validator checks the fraction and was never
    # reached from the grid, which had no ModelForm; this bound stays here and
    # the two now agree because there is only one writer.
    if not (Decimal("0") <= rate_pct <= Decimal("25")):
        messages.error(request, _("The rate must be between 0 and 25 percent."))
        return back
    period = (request.POST.get("period") or "").strip()
    if period not in SurplusPeriod.values:
        messages.error(request, _("Pick a period."))
        return back

    # Built unsaved rather than get_or_create: threshold_display is NOT NULL,
    # so the implicit INSERT would fail before the fields are assigned.
    policy = SurplusPolicy.objects.filter(asset=asset).first() or SurplusPolicy(asset=asset)
    policy.threshold_display = threshold
    policy.rate = rate_pct / Decimal("100")
    policy.period = period
    policy.active = bool(request.POST.get("active"))
    policy.save()
    messages.success(request, _("Holding fee saved."))
    return back
