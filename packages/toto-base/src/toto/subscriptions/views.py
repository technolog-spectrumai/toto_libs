"""Three pages: choose a plan, see yours, and change it.

Deliberately small. Everything that decides anything lives in ``services`` and
``gate``; these render it.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor

from . import plans as plans_registry
from . import services
from .models import Subscription, default_plan, plan_for


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
    # `eligible_plans`, read by the subscribe endpoint below through the same
    # predicate: a plan this person is not offered must be as unreachable by
    # POST as it is absent from here, or hiding is a bluff.
    #
    # An anonymous visitor gets the WHOLE ladder instead, rendered inert —
    # what a platform charges is not a secret, and the template already
    # answers "sign in to choose" where the buttons would be. Eligibility
    # scopes what a logged-in person may BUY, not what a stranger may read.
    catalogue = (services.eligible_plans(user) if user.is_authenticated
                 else plans_registry.public_plans())
    for plan in catalogue:
        quote = services.quote(user, plan)
        quote["entitlements"] = plan.feature_rows()
        quote["is_current"] = current is not None and plan.key == current.key
        # Why this card is here — the communities that brought the offer.
        # Empty for the default plan, and for staff seeing a plan nobody is
        # offered; the template says which rather than implying "public".
        quote["audience"] = services.offering_communities(user, plan)
        rows.append(quote)

    subscription = None
    if user.is_authenticated:
        subscription = Subscription.objects.filter(user=user).first()

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
                    .first())

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
        "entitlements": plan.feature_rows() if plan else [],
        "charges": charges,
        "grace_days": services.grace_days(),
    })


@login_required
@require_POST
def subscribe(request, plan_key):
    # The SAME predicate the page renders from: a plan offered to no community
    # this person is in must be as unreachable by POST as it is absent from
    # the page — 404, not 403, because a 403 would confirm the key exists.
    # An unknown key answers identically, for the same reason.
    if not services.is_eligible(request.user, plan_key):
        raise Http404("No such plan.")
    plan = plans_registry.plan(plan_key)
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
    of them. A COLUMN WITH NO TICKS REACHES NOBODY — that is the whole
    inversion, and the header says it in those words. The previous table read
    an empty column as "public", so an operator who saw one had no way to
    tell "offered to everyone" from "offered to no one"; now there is only
    one meaning and the page states it.
    """
    from django.http import HttpResponseForbidden

    if not _is_operator(request.user):
        return HttpResponseForbidden(_("Plan audiences are set by staff."))

    from toto.socialhub.models import Community

    from .models import CommunityPlanOffer

    if request.method == "POST":
        added, removed = services.set_offers(request.POST)
        if added or removed:
            messages.success(request, _(
                "Saved: %(added)d offer(s) added, %(removed)d removed.") % {
                    "added": added, "removed": removed})
        else:
            messages.info(request, _("Nothing changed."))
        return redirect("subscriptions:audience")

    # The columns come from the REGISTRY, not from a table: eligibility
    # administration is populated with plan keys, and no plan definition is
    # copied into the database to render this page.
    catalogue = plans_registry.all_plans()
    ticked = set(CommunityPlanOffer.objects.values_list("community_id", "plan_key"))
    rows = [{"community": community,
             "members": community.members.count(),
             "cells": [{"plan": plan,
                        "on": (community.pk, plan.key) in ticked}
                       for plan in catalogue]}
            for community in Community.objects.order_by("name")]
    offered = {plan_key for _c, plan_key in ticked}
    return _render(request, "subscriptions/audience.html", {
        "active_tab": "audience",
        # `offered` is False for a plan nobody can buy — the template paints
        # that as a warning rather than leaving a blank column to be misread.
        "plans": [{"plan": plan, "offered": plan.key in offered,
                   "is_default": plan.is_default}
                  for plan in catalogue],
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
