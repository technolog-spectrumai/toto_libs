"""Discipline's pages: a person's mana and its history (the balance over
time and every change to it, a page at a time), and the lists of sigils and
auras.

Everybody signed in reads their own mana. The lists are a superuser's to
read, and nothing on them is changed here: sigils and auras are made, changed
and assigned in the admin panel (``admin.py``). Granting mana is the one
door on these pages: a superuser's who is on the Superuser plan, both and
never one (``may_grant``, 2026-10-12).
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.apps import apps
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor

from . import config, services
from .models import Aura, CommunityAura, ManaEvent, Sigil
from .sources import ManaSource

#: The most that is granted in one go: a guard against a slipped key, not a
#: rule of the design.
MAX_AMOUNT = Decimal("1000000")

#: What the History tab may show, and no more: how far back, and how far
#: apart the points of the line are.
RANGES = {
    "day": (timedelta(days=1), timedelta(minutes=15)),
    "week": (timedelta(days=7), timedelta(hours=1)),
    "month": (timedelta(days=30), timedelta(hours=6)),
}
#: How many changes a page of the History tab's table holds.
CHANGES_A_PAGE = 20
#: How a point's time is written under the line, by range.
LABELS = {"day": "H:i", "week": "D H:i", "month": "j M H:i"}


def _render(request, template_name, context):
    context.setdefault("is_operator", is_operator(request.user))
    context.setdefault("may_grant", may_grant(request.user))
    return render(request, template_name, PageProcessor().decorate(context, request))


def is_operator(user) -> bool:
    return bool(getattr(user, "is_active", False) and getattr(user, "is_superuser", False))


def may_grant(user) -> bool:
    """Who grants mana: a superuser on the Superuser plan, both and never one
    (the owner, 2026-10-12: "only if you are in superuser plan then you can
    grant mana"; ``toto.subscriptions.models.superuser_plan_active``, the
    rule of Storage's Management tab, ``vault.plan_gate``). The account alone
    is refused. A host without ``toto.subscriptions`` sells no plan: there
    being a superuser is enough."""
    if not is_operator(user):
        return False
    if not apps.is_installed("toto.subscriptions"):
        return True
    from toto.subscriptions.models import superuser_plan_active

    return superuser_plan_active(user)


def _refused():
    return HttpResponseForbidden(_("Discipline is administered by superusers."))


def _plans_url() -> str:
    """Where a plan is taken, or "" on a host that sells none."""
    try:
        return reverse("subscriptions:plans")
    except NoReverseMatch:
        return ""


def _number(raw):
    """A non-negative number off a form; ValueError otherwise."""
    raw = (raw or "").strip().replace(",", ".")
    try:
        number = Decimal(raw)
    except InvalidOperation:
        raise ValueError(raw) from None
    if not number.is_finite() or number < 0 or number > MAX_AMOUNT:
        raise ValueError(raw)
    return number


def _person_named(raw):
    name = (raw or "").strip()
    return get_user_model().objects.filter(username__iexact=name).first() if name else None


def _admin_url(name: str) -> str:
    """The admin panel's list of a model, or "" where the host has no admin."""
    try:
        return reverse(f"admin:discipline_{name}_changelist")
    except NoReverseMatch:
        return ""


# ---------------------------------------------------------------------------
# A person's mana
# ---------------------------------------------------------------------------

def _prices():
    from toto.quota.metrics import registry

    rows = []
    for code, price in sorted(config.load().prices.items()):
        metric = registry.get(code)
        if metric is not None and price:
            rows.append({"label": metric.label, "unit": metric.unit, "price": price})
    return rows


@login_required
def index(request):
    user = request.user
    speed = services.speed_for(user)
    return _render(request, "discipline/index.html", {
        "active_tab": "mana",
        "balance": services.balance(user),
        "speed": speed,
        "community": speed.community,
        "sources": [source.describe(user) for source in ManaSource.all()
                    if source.available(user)],
        "prices": _prices(),
        "plans_url": _plans_url(),
    })


def _history_chart(user, chosen: str) -> str:
    """The balance over the chosen range, as the chart primitive's JSON
    (oya/partials/chart.html): one line, nothing else."""
    from toto.core import palette

    span, step = RANGES[chosen]
    points = services.balance_history(user, span=span, step=step)
    cyan = palette.colors().get("security-light", "#1f9db3")
    return json.dumps({
        "chart_type": "line",
        "labels": [date_format(timezone.localtime(at), LABELS[chosen]) for at, _have in points],
        "datasets": [{
            "label": _("Mana"), "data": [float(have) for _at, have in points],
            "borderColor": cyan, "backgroundColor": cyan + "33", "fill": True,
            "stepped": True, "pointRadius": 0, "borderWidth": 2,
        }],
        "options": {"interaction": {"mode": "index", "intersect": False},
                    "plugins": {"legend": {"display": False}},
                    "scales": {"y": {"beginAtZero": True}}},
    })


@login_required
def history(request):
    chosen = request.GET.get("range")
    if chosen not in RANGES:
        chosen = "day"
    # Every change to the person's mana, newest first, a page at a time: the
    # table under the line (it was the Mana page's last twenty until
    # 2026-10-12). The chosen range rides along in the pager's links.
    page = Paginator(ManaEvent.objects.filter(user=request.user), CHANGES_A_PAGE).get_page(
        request.GET.get("page"))
    return _render(request, "discipline/history.html", {
        "active_tab": "history",
        "events": page.object_list,
        "page_obj": page,
        "is_paginated": page.has_other_pages(),
        "extra_query": f"&range={chosen}",
        "changes_total": page.paginator.count,
        "range": chosen,
        "ranges": [("day", _("Last day")), ("week", _("Last week")), ("month", _("Last month"))],
        "balance": services.balance(request.user),
        "chart_json": _history_chart(request.user, chosen),
    })


@login_required
@require_POST
def grant(request):
    if not may_grant(request.user):
        return HttpResponseForbidden(
            _("Granting mana needs a superuser on the Superuser plan."))
    person = _person_named(request.POST.get("username"))
    if person is None:
        messages.error(request, _("There is no account with that username."))
        return redirect("discipline:index")
    try:
        amount = _number(request.POST.get("amount"))
        if amount <= 0:
            raise ValueError
    except ValueError:
        messages.error(request, _("The amount is a number greater than zero."))
        return redirect("discipline:index")
    event = services.grant(person, amount, by=request.user,
                           reason=(request.POST.get("reason") or "").strip())
    messages.success(request, _("%(amount)s mana granted to %(name)s; they have %(balance)s.") % {
        "amount": services._amount(event.delta), "name": person.get_username(),
        "balance": services._amount(event.balance_after)})
    return redirect("discipline:index")


# ---------------------------------------------------------------------------
# The lists: read here, changed in the admin panel
# ---------------------------------------------------------------------------

def _cell(text):
    return {"text": text}


@login_required
def sigils(request):
    if not is_operator(request.user):
        return _refused()
    rows = []
    for sigil in Sigil.objects.select_related("holder"):
        if sigil.generating:
            state = _("Generating")
        elif sigil.active:
            state = _("Held by nobody")
        else:
            state = _("Revoked")
        rows.append([
            {"type": "image", "url": sigil.image.url if sigil.image else "", "text": sigil.name},
            _cell(sigil.name), _cell(str(sigil.uuid)),
            _cell(services._amount(sigil.regen_per_hour)),
            _cell(sigil.holder.get_username() if sigil.holder_id else ""),
            _cell(state),
            _cell(date_format(timezone.localtime(sigil.assigned_at), "Y-m-d H:i")
                  if sigil.assigned_at and sigil.holder_id else ""),
        ])
    return _render(request, "discipline/sigils.html", {
        "active_tab": "sigils",
        "columns": [{"label": label} for label in (
            _("Picture"), _("Name"), "UUID", _("Mana an hour"), _("Holder"), _("State"),
            _("Assigned"))],
        "rows": rows,
        "admin_url": _admin_url("sigil"),
    })


@login_required
def auras(request):
    if not is_operator(request.user):
        return _refused()
    default_cap = config.load().cap
    names = {}
    for aura_id, name in (CommunityAura.objects.order_by("community__name")
                          .values_list("aura_id", "community__name")):
        names.setdefault(aura_id, []).append(name)
    rows = [[
        {"type": "image", "url": aura.image.url if aura.image else "", "text": aura.name},
        _cell(aura.name),
        _cell(services._amount(aura.regen_per_hour)),
        _cell(services._amount(aura.cap if aura.cap is not None else default_cap)),
        _cell(", ".join(names.get(aura.pk, []))),
    ] for aura in Aura.objects.all()]
    return _render(request, "discipline/auras.html", {
        "active_tab": "auras",
        "columns": [{"label": label} for label in (
            _("Picture"), _("Name"), _("Mana an hour"), _("Cap"), _("Communities"))],
        "rows": rows,
        "admin_url": _admin_url("aura"),
        "community_admin_url": _admin_url("communityaura"),
    })
