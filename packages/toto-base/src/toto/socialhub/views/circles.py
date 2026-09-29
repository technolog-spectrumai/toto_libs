"""The Circles tab (2026-09-29): where circles are managed, by superusers.

A circle is a Community with ``is_circle`` — ``seniors``, ``board`` — that
decides who reads wiki pages (and what else an app gates by circles) and
how fast its members' mana refills; it carries no plan, discount or right,
and members never see it listed (README, "Functional communities and
circles"). Here a superuser makes one (at most ``MAX_CIRCLES``), puts people
in and takes them out — ``Person.communities``, the one membership list —
and sets its refill speed per pool. Every change reaches the audit chain
through ``audit.py``'s signals, whichever door made it.

What a circle READS is each app's own business and stays there: the wiki's
page × circle grid, a sheet's or a place's access control. This page links
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
from toto.socialhub.models import MAX_CIRCLES, REGEN_POOLS, Community
from toto.ui import PageProcessor


def _refused(request):
    return HttpResponseForbidden(_("Circles are managed by superusers."))


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _person(key: str):
    """A Person by slug or by their account's username; None when neither."""
    key = (key or "").strip()
    if not key:
        return None
    return (Person.objects.filter(slug=key).first()
            or Person.objects.filter(user__username=key).first())


def _circle_or_404(pk):
    return get_object_or_404(Community.objects.circles(), pk=pk)


@login_required
@require_safe
def circles(request):
    if not request.user.is_superuser:
        return _refused(request)
    rows = []
    for circle in Community.objects.circles().order_by("name"):
        rows.append({
            "circle": circle,
            "members": list(circle.members.select_related("user").order_by("display_name")),
            "speeds": [{"pool": pool, "value": getattr(circle, f"regen_{pool}")}
                       for pool in REGEN_POOLS],
        })
    return _render(request, "socialhub/circles.html", {
        "rows": rows,
        "pools": REGEN_POOLS,
        "max_circles": MAX_CIRCLES,
        "room_left": max(0, MAX_CIRCLES - len(rows)),
        "people": Person.objects.filter(user__isnull=False).select_related("user")
                  .order_by("display_name")[:2000],
        "active_tab": "circles",
    })


@login_required
@require_POST
def circle_add(request):
    if not request.user.is_superuser:
        return _refused(request)
    name = " ".join((request.POST.get("name") or "").split())[:120]
    if not name:
        messages.error(request, _("A circle needs a name."))
    elif Community.objects.filter(name__iexact=name).exists():
        messages.error(request, _("There is already a community or circle called %(name)s.")
                       % {"name": name})
    else:
        try:
            # The model holds the cap (MAX_CIRCLES) and says so; this only relays it.
            Community.objects.create(name=name, is_circle=True)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, _("Circle %(name)s made. Put people in it below.")
                             % {"name": name})
    return redirect("socialhub:circles")


@login_required
@require_POST
def circle_member(request, pk):
    """Put a person in a circle, or take them out — ``Person.communities``,
    the one membership list; the audit chain sees it (audit.py)."""
    if not request.user.is_superuser:
        return _refused(request)
    circle = _circle_or_404(pk)
    if request.POST.get("action") == "remove":
        who = (request.POST.get("person") or "").strip()
        person = Person.objects.filter(pk=int(who)).first() if who.isascii() and who.isdigit() else None
        if person is not None:
            circle.members.remove(person)
            messages.success(request, _("%(who)s left %(circle)s.")
                             % {"who": person.display_name, "circle": circle.name})
    else:
        key = request.POST.get("who") or ""
        person = _person(key)
        if person is None:
            messages.error(request, _("There is nobody called “%(who)s”.") % {"who": key.strip()[:80]})
        else:
            circle.members.add(person)
            messages.success(request, _("%(who)s is in %(circle)s.")
                             % {"who": person.display_name, "circle": circle.name})
    return redirect("socialhub:circles")


@login_required
@require_POST
def circle_speeds(request, pk):
    """The circle's refill speed per pool: a number, or blank for the pool's
    own rate. Refused as the model refuses it (negative, not a number)."""
    if not request.user.is_superuser:
        return _refused(request)
    circle = _circle_or_404(pk)
    for pool in REGEN_POOLS:
        raw = (request.POST.get(f"regen_{pool}") or "").strip().replace(",", ".")
        if not raw:
            setattr(circle, f"regen_{pool}", None)
            continue
        try:
            value = Decimal(raw)
        except InvalidOperation:
            messages.error(request, _("%(value)s is not a number.") % {"value": raw[:20]})
            return redirect("socialhub:circles")
        setattr(circle, f"regen_{pool}", value)
    try:
        circle.full_clean()
    except ValidationError as exc:
        messages.error(request, " ".join(m for errors in exc.message_dict.values() for m in errors))
        return redirect("socialhub:circles")
    circle.save()
    messages.success(request, _("Speeds saved for %(circle)s; they apply from the next hourly refill.")
                     % {"circle": circle.name})
    return redirect("socialhub:circles")


@login_required
@require_POST
def circle_delete(request, pk):
    """A circle goes — unless something still reads through it (an app's
    PROTECT), in which case the refusal says so and nothing changes."""
    from django.db.models import ProtectedError

    if not request.user.is_superuser:
        return _refused(request)
    circle = _circle_or_404(pk)
    name = circle.name
    try:
        circle.delete()
    except ProtectedError:
        messages.error(request, _("%(circle)s still decides who reads something. Take it off "
                                  "those first.") % {"circle": name})
    else:
        messages.success(request, _("Circle %(circle)s removed.") % {"circle": name})
    return redirect("socialhub:circles")
