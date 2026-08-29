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
    # `visible_plans`, not `.filter(active=True)`: a plan with audience rows
    # is offered only to members of those communities, and this page and the
    # subscribe endpoint below must read the SAME queryset or hiding is a
    # bluff. The card says which community brought the offer — a plan that
    # appears for reasons the reader cannot see looks like a pricing bug.
    for plan in services.visible_plans(user).prefetch_related(
            "audiences__community"):
        quote = services.quote(user, plan)
        quote["entitlements"] = plan.entitlement_rows()
        quote["is_current"] = current is not None and plan.pk == current.pk
        quote["audience"] = [a.community.name for a in plan.audiences.all()]
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
    # The visible queryset, NOT `active=True`: a plan offered to communities
    # this user is in none of must be as unreachable by POST as it is absent
    # from the page — 404, the same answer a stranger gets for a draft
    # bounty, because a 403 would confirm the code exists.
    plan = get_object_or_404(services.visible_plans(request.user), code=code)
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


def _is_operator(user) -> bool:
    """``is_superuser`` does not imply ``is_staff`` in Django. Both count."""
    return bool(user.is_staff or user.is_superuser)


@login_required
def audience(request):
    """Which communities each plan is offered to — the Communities tab.

    Operators only, like Discounts, and for the same reason: this decides
    what people can BUY, and a page that shows the dial to someone who
    cannot turn it is just a tease.

    The grid is communities × plans, plans as columns because there are few
    of them. A COLUMN with no ticks anywhere is a public plan — the page
    says so in the header, because "restricted to nobody" and "offered to
    everyone" being the same state is the one thing an operator must not
    have to deduce.
    """
    from django.http import HttpResponseForbidden

    if not _is_operator(request.user):
        return HttpResponseForbidden(_("Plan audiences are set by staff."))

    from toto.socialhub.models import Community

    from .models import PlanAudience

    if request.method == "POST":
        added, removed = services.set_audiences(request.POST)
        if added or removed:
            messages.success(request, _(
                "Saved: %(added)d audience(s) added, %(removed)d removed.") % {
                    "added": added, "removed": removed})
        else:
            messages.info(request, _("Nothing changed."))
        return redirect("subscriptions:audience")

    plans = list(SubscriptionPlan.objects.filter(active=True))
    ticked = set(PlanAudience.objects.values_list("community_id", "plan_id"))
    rows = [{"community": community,
             "members": community.members.count(),
             "cells": [{"plan": plan,
                        "on": (community.pk, plan.pk) in ticked}
                       for plan in plans]}
            for community in Community.objects.order_by("name")]
    restricted = {plan_id for _c, plan_id in ticked}
    return _render(request, "subscriptions/audience.html", {
        "active_tab": "audience",
        "plans": [{"plan": plan, "restricted": plan.pk in restricted}
                  for plan in plans],
        "rows": rows,
    })


def discounts(request):
    """Set what each community takes off its members' subscriptions.

    Operators only, and the tab does not render for anybody else — this
    decides what people are charged, and a page that shows the dial to
    someone who cannot turn it is just a tease.

    Every community is listed, including the ones at zero: "which communities
    have a discount" is a question answered by reading one column, not by
    remembering which rows exist. A zero saves as no row at all, so the table
    stays the set of communities that actually give something.
    """
    from django.http import HttpResponseForbidden

    if not _is_operator(request.user):
        return HttpResponseForbidden(_("Community discounts are set by staff."))

    from toto.socialhub.models import Community

    from .models import CommunityDiscount

    if request.method == "POST":
        saved, cleared = services.set_discounts(request.POST)
        if saved or cleared:
            messages.success(request, _(
                "Saved: %(saved)d discount(s), %(cleared)d cleared.") % {
                    "saved": saved, "cleared": cleared})
        else:
            messages.info(request, _("Nothing changed."))
        return redirect("subscriptions:discounts")

    by_community = {d.community_id: d.percent
                    for d in CommunityDiscount.objects.all()}
    rows = [{"community": community,
             "percent": by_community.get(community.pk, 0),
             "members": community.members.count()}
            for community in Community.objects.order_by("name")]
    return _render(request, "subscriptions/discounts.html", {
        "active_tab": "discounts",
        "rows": rows,
    })
