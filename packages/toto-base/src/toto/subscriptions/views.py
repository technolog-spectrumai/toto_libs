"""Three pages: choose a plan, see yours, and change it.

Deliberately small. Everything that decides anything lives in ``services`` and
``gate``; these render it.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor

from . import services
from .models import Subscription, SubscriptionPlan, default_plan, plan_for


def _render(request, template_name, context):
    return render(request, template_name,
                  PageProcessor().decorate(context, request))


def plans(request):
    """Every plan, priced for whoever is looking.

    Not ``@login_required``: what a platform charges is not a secret, and a
    person deciding whether to join should be able to read it first. An
    anonymous visitor simply gets no discount and no "your plan" marker.
    """
    user = request.user
    current = plan_for(user) if user.is_authenticated else None

    rows = []
    for plan in SubscriptionPlan.objects.filter(active=True):
        quote = services.quote(user, plan)
        quote["entitlements"] = plan.entitlement_rows()
        quote["is_current"] = current is not None and plan.pk == current.pk
        rows.append(quote)

    subscription = None
    if user.is_authenticated:
        subscription = (Subscription.objects.filter(user=user)
                        .select_related("plan").first())

    return _render(request, "subscriptions/plans.html", {
        "active_tab": "plans",
        "rows": rows,
        "subscription": subscription,
        "current_plan": current,
        # None when nothing on this host is priced. The template says which,
        # because "free" and "we do not bill here" are different statements.
        "priced": any(row["priced"] for row in rows),
    })


@login_required
def mine(request):
    """Your plan, your next month, and every month you have been billed."""
    subscription = (Subscription.objects.filter(user=request.user)
                    .select_related("plan").first())

    charges = []
    if subscription is not None:
        # Materialise on render, the way tribute did: it is idempotent by
        # (subscription, period_label), so a page that has not been opened for
        # three months catches up without a task having run. It does NOT settle
        # — a page render must never reach into a wallet.
        services.materialize(subscription)
        charges = list(subscription.charges.all()[:24])

    plan = plan_for(request.user)
    return _render(request, "subscriptions/mine.html", {
        "active_tab": "mine",
        "subscription": subscription,
        "plan": plan,
        "quote": services.quote(request.user, plan),
        "entitlements": plan.entitlement_rows() if plan else [],
        "charges": charges,
        "grace_days": services.grace_days(),
    })


@login_required
@require_POST
def subscribe(request, code):
    plan = get_object_or_404(SubscriptionPlan, code=code, active=True)
    services.subscribe(request.user, plan)
    messages.success(request, _("You are on the %(plan)s plan.") % {"plan": plan.name})
    return redirect("subscriptions:mine")


@login_required
@require_POST
def cancel(request):
    """Stop paying. Access falls back to the free plan; nothing is deleted."""
    subscription = Subscription.objects.filter(user=request.user).first()
    if subscription is None:
        return redirect("subscriptions:plans")
    services.cancel(subscription)
    free = default_plan()
    messages.info(request, _("Your subscription is cancelled. You keep %(plan)s, "
                             "and everything you have made stays where it is.")
                  % {"plan": free.name if free else _("the free features")})
    return redirect("subscriptions:mine")
