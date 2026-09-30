"""The Clearances tab (2026-09-29): where clearances are made and removed, by
superusers.

A clearance (``socialhub.Clearance`` — ``internal``, ``confidential``, named
after what it opens) decides who reads wiki pages (and what else an app
gates by clearances) and how fast its holders' mana refills; it carries no
plan, discount or right, and members never see it listed (README,
"Communities and clearances").

The page (2026-09-30) is a paginated list — a table on a wide screen, cards
on a narrow one — with two doors and no others:

* **New clearance**, a modal: its name, its refill speed per pool, and who
  holds it, found by searching people (``clearance_people``, JSON). Made in
  one transaction, so a refused speed or the cap leaves nothing half-made.
  A refusal is Post/Redirect/Get: what was typed and why it was refused go
  to the session, and the list re-opens the modal with them — so every link
  on that page (pagination, the language switcher) stays on the list.
* **Delete**, refused while an app still keeps something to the clearance
  (their through tables PROTECT it).

Nothing is edited here: who holds a clearance and its speeds change in the
Django admin (``ClearanceAdmin``, superusers only). Every change reaches the
audit chain through ``audit.py``'s signals, whichever door made it.

What a clearance READS is each app's own business and stays there: the wiki's
page × clearance grid, a sheet's or a place's access control.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import models, transaction
from django.db.models import Count, Prefetch, Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST, require_safe

from toto.people.models import Person
from toto.socialhub.models import MAX_CLEARANCES, REGEN_POOLS, Clearance
from toto.ui import PageProcessor

#: Clearances per page. The platform holds at most MAX_CLEARANCES (7), so
#: five a page is what makes the pagination more than decoration.
PER_PAGE = 5
#: Holders named on a row before "+N more".
HOLDERS_SHOWN = 4
#: People one search answers with.
PEOPLE_LIMIT = 20
#: Where a refused New clearance waits for the list to draw it.
DRAFT_KEY = "socialhub.clearance_draft"


def _refused(request):
    return HttpResponseForbidden(_("Clearances are managed by superusers."))


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _clearance_or_404(pk):
    return get_object_or_404(Clearance, pk=pk)


def _ids(values) -> set:
    """Person pks from a form: ASCII digits only, anything else ignored."""
    return {int(v) for v in values
            if isinstance(v, str) and v.isascii() and v.isdigit() and len(v) <= 18}


def _keeping_relations() -> list:
    """The through tables that keep things to a clearance — every app's
    ``*Clearance`` row (wiki pages, vault files, routes, layers, places),
    found by their PROTECT on the clearance rather than named, so an app
    installed or not changes nothing here."""
    return [rel for rel in Clearance._meta.related_objects
            if rel.one_to_many and rel.on_delete is models.PROTECT]


def _kept_by(clearance, relations) -> int:
    return sum(rel.related_model._default_manager.filter(**{rel.field.name: clearance}).count()
               for rel in relations)


def _read_speeds(data) -> tuple[dict, str]:
    """``({pool: Decimal | None}, error)`` from ``regen_<pool>`` fields: blank
    is the pool's own rate, a comma is a decimal point."""
    speeds = {}
    for pool in REGEN_POOLS:
        raw = (data.get(f"regen_{pool}") or "").strip().replace(",", ".")
        if not raw:
            speeds[pool] = None
            continue
        try:
            speeds[pool] = Decimal(raw)
        except InvalidOperation:
            return speeds, _("%(value)s is not a number.") % {"value": raw[:20]}
    return speeds, ""


def _person_row(person) -> dict:
    return {"pk": person.pk, "name": person.display_name,
            "username": person.user.username if person.user_id else ""}


def _page(request, *, draft=None):
    """The list, one page of it; ``draft`` re-opens the New clearance modal
    with what a refused submission carried."""
    relations = _keeping_relations()
    # Only the holders a row names are loaded (a sliced prefetch); the count
    # comes from the annotation, however many there are.
    listing = (Clearance.objects.order_by("name")
               .annotate(holder_count=Count("members", distinct=True))
               .prefetch_related(Prefetch(
                   "members",
                   queryset=Person.objects.order_by("display_name", "pk")
                   .only("pk", "display_name")[:HOLDERS_SHOWN],
                   to_attr="shown_holders")))
    total = Clearance.objects.count()
    page = Paginator(listing, PER_PAGE).get_page(request.GET.get("page"))
    rows = []
    for clearance in page:
        rows.append({
            "clearance": clearance,
            "holders": clearance.shown_holders,
            "more_holders": max(0, clearance.holder_count - HOLDERS_SHOWN),
            "speeds": [{"pool": pool, "value": getattr(clearance, f"regen_{pool}")}
                       for pool in REGEN_POOLS],
            "kept": _kept_by(clearance, relations),
        })
    return _render(request, "socialhub/clearances.html", {
        "rows": rows,
        "page_obj": page,
        "is_paginated": page.has_other_pages(),
        "extra_query": "",
        "total": total,
        "pools": REGEN_POOLS,
        "max_clearances": MAX_CLEARANCES,
        "room_left": max(0, MAX_CLEARANCES - total),
        "draft": draft or {"open": False, "name": "", "speeds": {}, "people": [], "error": ""},
        "active_tab": "clearances",
    })


@login_required
@require_safe
def clearances(request):
    if not request.user.is_superuser:
        return _refused(request)
    return _page(request, draft=request.session.pop(DRAFT_KEY, None))


@login_required
@require_safe
def clearance_people(request):
    """People to give a clearance to, by a piece of their name, username,
    slug or e-mail — JSON for the New clearance modal. Only people with an
    account: a clearance opens things to somebody who signs in."""
    if not request.user.is_superuser:
        return JsonResponse({"error": _("Clearances are managed by superusers.")}, status=403)
    q = " ".join((request.GET.get("q") or "").split())[:80]
    if not q:
        return JsonResponse({"people": []})
    found = (Person.objects.filter(user__isnull=False)
             .filter(Q(display_name__icontains=q) | Q(slug__icontains=q)
                     | Q(user__username__icontains=q) | Q(user__email__icontains=q))
             .select_related("user").order_by("display_name")[:PEOPLE_LIMIT])
    return JsonResponse({"people": [_person_row(person) for person in found]})


@login_required
@require_POST
def clearance_add(request):
    """Make a clearance with its speeds and its holders, all or nothing."""
    if not request.user.is_superuser:
        return _refused(request)
    name = " ".join((request.POST.get("name") or "").split())[:120]
    speeds, error = _read_speeds(request.POST)
    people = list(Person.objects.filter(pk__in=_ids(request.POST.getlist("person")),
                                        user__isnull=False)
                  .select_related("user").order_by("display_name"))
    if not error and not name:
        error = _("A clearance needs a name.")
    elif not error and Clearance.objects.filter(name__iexact=name).exists():
        error = _("There is already a clearance called %(name)s.") % {"name": name}
    if not error:
        clearance = Clearance(name=name, **{f"regen_{pool}": value for pool, value in speeds.items()})
        try:
            # The model holds the cap and the speed limits (MAX_CLEARANCES,
            # non-negative, 12 digits / 4 places) and says so; this relays it.
            clearance.full_clean(exclude=["slug"])
            with transaction.atomic():
                clearance.save()
                if people:
                    clearance.members.add(*people)
        except ValidationError as exc:
            error = " ".join(m for errors in exc.message_dict.values() for m in errors)
    if error:
        request.session[DRAFT_KEY] = {
            "open": True, "name": name, "error": error,
            "speeds": {pool: (request.POST.get(f"regen_{pool}") or "").strip()[:20]
                       for pool in REGEN_POOLS},
            "people": [_person_row(person) for person in people],
        }
        return redirect("socialhub:clearances")
    if people:
        messages.success(request, _("Clearance %(name)s made, held by %(n)d.")
                         % {"name": name, "n": len(people)})
    else:
        messages.success(request, _("Clearance %(name)s made.") % {"name": name})
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
