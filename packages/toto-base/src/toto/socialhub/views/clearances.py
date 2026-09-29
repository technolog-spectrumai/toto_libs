"""The Clearances tab (2026-09-29): where clearances are managed, by superusers.

A clearance (``socialhub.Clearance`` — ``internal``, ``confidential``, named
after what it opens) decides who reads wiki pages (and what else an app
gates by clearances) and how fast its holders' mana refills; it carries no
plan, discount or right, and members never see it listed (README,
"Communities and clearances"). Here a superuser makes one (at most
``MAX_CLEARANCES``), puts people in and takes them out — ``Person.clearances``
— and sets its refill speed per pool. Every change reaches the audit chain
through ``audit.py``'s signals, whichever door made it.

What a clearance READS is each app's own business and stays there: the wiki's
page × clearance grid, a sheet's or a place's access control. This page links
to those where they exist.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST, require_safe

from toto.people.models import Person
from toto.socialhub.models import MAX_CLEARANCES, REGEN_POOLS, Clearance
from toto.ui import PageProcessor


def _refused(request):
    return HttpResponseForbidden(_("Clearances are managed by superusers."))


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _person(key: str):
    """A Person by slug or by their account's username; None when neither."""
    key = (key or "").strip()
    if not key:
        return None
    return (Person.objects.filter(slug=key).first()
            or Person.objects.filter(user__username=key).first())


def _clearance_or_404(pk):
    return get_object_or_404(Clearance, pk=pk)


@login_required
@require_safe
def clearances(request):
    if not request.user.is_superuser:
        return _refused(request)
    rows = []
    for clearance in Clearance.objects.order_by("name"):
        rows.append({
            "clearance": clearance,
            "members": list(clearance.members.select_related("user").order_by("display_name")),
            "speeds": [{"pool": pool, "value": getattr(clearance, f"regen_{pool}")}
                       for pool in REGEN_POOLS],
        })
    return _render(request, "socialhub/clearances.html", {
        "rows": rows,
        "pools": REGEN_POOLS,
        "max_clearances": MAX_CLEARANCES,
        "room_left": max(0, MAX_CLEARANCES - len(rows)),
        "people": Person.objects.filter(user__isnull=False).select_related("user")
                  .order_by("display_name")[:2000],
        "active_tab": "clearances",
    })


@login_required
@require_POST
def clearance_add(request):
    if not request.user.is_superuser:
        return _refused(request)
    name = " ".join((request.POST.get("name") or "").split())[:120]
    if not name:
        messages.error(request, _("A clearance needs a name."))
    elif Clearance.objects.filter(name__iexact=name).exists():
        messages.error(request, _("There is already a clearance called %(name)s.")
                       % {"name": name})
    else:
        try:
            # The model holds the cap (MAX_CLEARANCES) and says so; this only relays it.
            Clearance.objects.create(name=name)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, _("Clearance %(name)s made. Put people in it below.")
                             % {"name": name})
    return redirect("socialhub:clearances")


@login_required
@require_POST
def clearance_member(request, pk):
    """Put a person in a clearance, or take them out — ``Person.clearances``;
    the audit chain sees it (audit.py)."""
    if not request.user.is_superuser:
        return _refused(request)
    clearance = _clearance_or_404(pk)
    if request.POST.get("action") == "remove":
        who = (request.POST.get("person") or "").strip()
        person = Person.objects.filter(pk=int(who)).first() if who.isascii() and who.isdigit() else None
        if person is not None:
            clearance.members.remove(person)
            messages.success(request, _("%(who)s left %(clearance)s.")
                             % {"who": person.display_name, "clearance": clearance.name})
    else:
        key = request.POST.get("who") or ""
        person = _person(key)
        if person is None:
            messages.error(request, _("There is nobody called “%(who)s”.") % {"who": key.strip()[:80]})
        else:
            clearance.members.add(person)
            messages.success(request, _("%(who)s is in %(clearance)s.")
                             % {"who": person.display_name, "clearance": clearance.name})
    return redirect("socialhub:clearances")


@login_required
@require_POST
def clearance_speeds(request, pk):
    """The clearance's refill speed per pool: a number, or blank for the pool's
    own rate. Refused as the model refuses it (negative, not a number)."""
    if not request.user.is_superuser:
        return _refused(request)
    clearance = _clearance_or_404(pk)
    for pool in REGEN_POOLS:
        raw = (request.POST.get(f"regen_{pool}") or "").strip().replace(",", ".")
        if not raw:
            setattr(clearance, f"regen_{pool}", None)
            continue
        try:
            value = Decimal(raw)
        except InvalidOperation:
            messages.error(request, _("%(value)s is not a number.") % {"value": raw[:20]})
            return redirect("socialhub:clearances")
        setattr(clearance, f"regen_{pool}", value)
    try:
        clearance.full_clean()
    except ValidationError as exc:
        messages.error(request, " ".join(m for errors in exc.message_dict.values() for m in errors))
        return redirect("socialhub:clearances")
    clearance.save()
    messages.success(request, _("Speeds saved for %(clearance)s; they apply from the next hourly refill.")
                     % {"clearance": clearance.name})
    return redirect("socialhub:clearances")


@login_required
@require_POST
def clearance_delete(request, pk):
    """A clearance goes — unless something still reads through it (an app's
    PROTECT), in which case the refusal says so and nothing changes."""
    from django.db.models import ProtectedError

    if not request.user.is_superuser:
        return _refused(request)
    clearance = _clearance_or_404(pk)
    name = clearance.name
    try:
        clearance.delete()
    except ProtectedError:
        messages.error(request, _("%(clearance)s still decides who reads something. Take it off "
                                  "those first.") % {"clearance": name})
    else:
        messages.success(request, _("Clearance %(clearance)s removed.") % {"clearance": name})
    return redirect("socialhub:clearances")
