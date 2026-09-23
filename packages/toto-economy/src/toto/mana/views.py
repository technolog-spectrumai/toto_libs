"""The member's view of mana — progressive disclosure, four levels.

L0 is the header chip (``plugins/header_plugins.py``). L1 is ``index``: three
bars, one sentence each. L2 is ``colour``: one pool in depth, with its detail
folded away until asked for. L3 is ``about``: how it all works, and — for staff
only — the way into the full economy. Nothing here writes.
"""

from __future__ import annotations

import json
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse

from toto.ui import PageProcessor

from . import colours, services, status


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _is_staff(user) -> bool:
    return bool(getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))


def _cards(user, balances):
    """The three pools in fixed order, each with its one sentence."""
    plain_total = services.plain_files(user, limit=0)[1] if balances else 0
    cards = []
    for role in colours.ROLES:
        b = (balances or {}).get(role)
        if b is None:
            continue
        cards.append({**b, "plain_total": plain_total if role == "security" else 0})
    return cards


def _prices(role=None):
    """What changes a pool: priced actions (−), levies (−/day), earning (+)."""
    from toto.quota import rates
    from toto.quota.metrics import registry

    levies = {c for codes in services.LEVY_OF.values() for c in codes}
    card = rates.rate_card()
    rows = []
    for code, row_role in sorted(colours.COLOUR_OF.items()):
        if role is not None and row_role != role:
            continue
        price, metric = card.get(code), registry.get(code)
        if price is None or metric is None:
            continue
        per = Decimal(price["price_display"]) / Decimal(price.get("unit_quantity") or 1)
        rows.append({"role": row_role, "code": code, "label": str(metric.label),
                     "amount": per, "unit": metric.unit,
                     "kind": "levy" if code in levies else "cost"})
    if role in (None, "security"):
        rows.append({"role": "security", "code": "", "label": "",
                     "amount": colours.ENCRYPT_REWARD, "unit": "file",
                     "kind": "earn"})
    return rows


@login_required
def index(request):
    balances = services.balances_of(request.user)
    return _render(request, "mana/index.html", {
        "cards": _cards(request.user, balances),
        "next_tick_at": services.next_tick_at(),
        "active_tab": "mana",
    })


def _chart(points, maximum):
    """An SVG polyline for a 300×64 box, oldest left."""
    if not points or not maximum:
        return ""
    width, height, n = 300, 64, max(len(points) - 1, 1)
    coords = []
    for i, p in enumerate(points):
        x = round(i * width / n, 1)
        y = round(height - 2 - float(p["level"] / maximum) * (height - 4), 1)
        coords.append(f"{x},{y}")
    return " ".join(coords)


@login_required
def colour(request, colour):
    if colour not in colours.ROLES:
        raise Http404("No such pool.")
    balances = services.balances_of(request.user) or {}
    b = balances.get(colour)
    if b is None:
        raise Http404("This pool is not set up on this platform.")
    history = services.history(request.user, colour, limit=50)
    points = services.series(request.user, colour)
    context = {
        "b": b, "colour": colour, "history": history,
        "chart": _chart(points, b["max"]),
        "chart_low": min((p["level"] for p in points), default=b["amount"]),
        "prices": _prices(colour),
        "next_tick_at": services.next_tick_at(),
        "active_tab": "mana",
        "prompt_open": False,
    }
    if colour == "security":
        files, total = services.plain_files(request.user, limit=10)
        context.update({
            "plain_files": files, "plain_total": total,
            "plain_more": max(0, total - len(files)),
            "preselected": status.preselect(files, b["regen_per_day"]),
            "prompt_open": bool(files) and (
                b["band"] != "ok" or request.GET.get("encrypt") == "1"),
        })
    return _render(request, "mana/colour.html", context)


@login_required
def about(request):
    balances = services.balances_of(request.user) or {}
    staff_links = []
    if _is_staff(request.user):
        for name, label in (("assets:asset_list", "Ledger"),
                            ("quota:index", "Metered"),
                            ("tariffs:tariff_list", "Tariffs"),
                            ("bourse:exchange_center", "Exchange")):
            try:
                staff_links.append({"url": reverse(name), "label": label})
            except NoReverseMatch:
                continue
    return _render(request, "mana/about.html", {
        "cards": [balances[r] for r in colours.ROLES if r in balances],
        "prices": _prices(),
        "encrypt_reward": colours.ENCRYPT_REWARD,
        "encrypt_daily_cap": colours.ENCRYPT_DAILY_CAP,
        "staff_links": staff_links,
        "active_tab": "mana",
    })


def _num(value) -> float:
    return float(round(Decimal(value), 2))


def api_balances(request):
    """What the header chip polls. 401 as JSON, never a login redirect."""
    if not request.user.is_authenticated:
        return JsonResponse({"ok": False, "detail": "Sign in."}, status=401)
    balances = services.balances_of(request.user) or {}
    last = services.history(request.user, limit=1)
    payload = {
        "ok": True,
        "colours": {
            role: {"label": b["label"], "amount": _num(b["amount"]),
                   "max": _num(b["max"]), "pct": b["pct"], "band": b["band"],
                   "trend": b["trend"], "regen_per_hour": _num(b["regen_per_hour"]),
                   "net_per_day": _num(b["net_per_day"]),
                   "eta_full_hours": b["eta_full_hours"],
                   "eta_empty_hours": b["eta_empty_hours"]}
            for role, b in balances.items()
        },
        "lowest": services.lowest(balances),
        "last": ({"at": last[0]["at"].isoformat(), "role": last[0]["role"],
                  "delta": _num(last[0]["delta"]), "kind": last[0]["kind"],
                  "label": last[0]["label"]} if last else None),
    }
    response = JsonResponse(payload)
    response["Cache-Control"] = "no-store"
    return response


def chip_status(balances) -> str:
    """The chip's initial state as JSON, so it paints before its first poll."""
    return json.dumps({
        role: {"amount": _num(b["amount"]), "max": _num(b["max"]), "pct": b["pct"],
               "band": b["band"], "trend": b["trend"], "label": b["label"]}
        for role, b in (balances or {}).items()
    })
