"""The Clearances tab (2026-09-29): where clearances are made and removed, by
superusers — on the Superuser plan since 2026-10-01 (``may_manage``).

A clearance (``socialhub.Clearance`` — ``internal``, ``confidential``, named
after what it opens) decides who reads the files of a bucket and the map
items of a domain (whatever an app gates by clearances) and how fast its holders' mana refills; it carries no
plan, discount or right, and members never see it listed (README,
"Communities and clearances").

The page (2026-09-30) is a paginated list — a table on a wide screen, cards
on a narrow one — with two doors and no others:

* **New clearance**, a modal: its name, its refill speed per pool, who holds
  it (found by searching people, ``clearance_people``, JSON) and what it
  clears — the things it keeps, of every kind an app offers through a
  ``ClearanceTargetPlugin`` (``plugins/clearance_plugins.py``; searched by
  ``clearance_targets``, JSON). Made in one transaction, so a refused speed or the cap leaves nothing half-made.
  A refusal is Post/Redirect/Get: what was typed and why it was refused go
  to the session, and the list re-opens the modal with them — so every link
  on that page (pagination, the language switcher) stays on the list.
* **Delete**, refused while an app still keeps something to the clearance
  (their through tables PROTECT it).

Nothing is edited here: who holds a clearance and its speeds change in the
Django admin (``ClearanceAdmin``; since 2026-10-02 it asks this page's rule,
``may_manage``, and so does the person admin's ``clearances`` field). Every
change reaches the audit chain through ``audit.py``'s signals, whichever door
made it.

What a clearance READS is each app's own business and stays there: the
vault's bucket gate, the map's domain gate.
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
from django.utils.translation import ngettext
from django.views.decorators.http import require_POST, require_safe

from toto.people.models import Person
from toto.socialhub.models import MAX_CLEARANCES, REGEN_POOLS, Clearance
from toto.socialhub.plugins import clearance_plugins
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


def may_manage(user) -> bool:
    """Who makes and removes clearances and decides what they keep: a real
    superuser on the Superuser plan (2026-10-01, the review of stage 37c).
    The superuser bit alone opened every door here, and through it the doors
    behind, which ask the plan themselves — a bucket's clearances
    (``vault.clearances.may_manage``, 37c.1), a map domain's
    (``locations.access.may_manage_domains``) — since the plugins set those through the
    apps' own setters. ``contact_access.is_administrator`` is that rule."""
    from toto.socialhub.contact_access import is_administrator

    return is_administrator(user)


def _refusal(user) -> str:
    """A superuser without the plan is told what is missing; anybody else
    hears that clearances are the superusers'."""
    if getattr(user, "is_superuser", False):
        return _("This needs a superuser on the Superuser plan.")
    return _("Clearances are managed by superusers.")


def _refused(request):
    return HttpResponseForbidden(_refusal(request.user))


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
    ``*Clearance`` row (a vault bucket's, a map domain's),
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


def _read_targets(values) -> dict:
    """``{plugin: [objects]}`` from ``target=<kind key>:<pk>`` fields: an
    unknown kind, a malformed value or a pk that does not exist is ignored."""
    wanted: dict = {}
    for value in values:
        key, _sep, pk = (value or "").rpartition(":")
        plugin = clearance_plugins.kind(key) if key else None
        if plugin is not None and pk.isascii() and pk.isdigit() and len(pk) <= 18:
            wanted.setdefault(plugin, set()).add(int(pk))
    return {plugin: objects for plugin, pks in wanted.items()
            if (objects := plugin.resolve(pks))}


def _target_rows(targets) -> list:
    return [{"key": plugin.get_key(), "kind": str(plugin.title), "icon": plugin.icon, **plugin.row(obj)}
            for plugin, objects in targets.items() for obj in objects]


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
        "draft": draft or {"open": False, "name": "", "speeds": {}, "people": [], "targets": [],
                           "error": ""},
        "target_kinds": [{"key": plugin.get_key(), "title": str(plugin.title), "icon": plugin.icon}
                         for plugin in clearance_plugins.kinds()],
        "active_tab": "clearances",
    })


@login_required
@require_safe
def clearances(request):
    if not may_manage(request.user):
        return _refused(request)
    return _page(request, draft=request.session.pop(DRAFT_KEY, None))


@login_required
@require_safe
def clearance_people(request):
    """People to give a clearance to, by a piece of their name, username,
    slug or e-mail — JSON for the New clearance modal. Only people with an
    account: a clearance opens things to somebody who signs in."""
    if not may_manage(request.user):
        return JsonResponse({"error": _refusal(request.user)}, status=403)
    q = " ".join((request.GET.get("q") or "").split())[:80]
    if not q:
        return JsonResponse({"people": []})
    found = (Person.objects.filter(user__isnull=False)
             .filter(Q(display_name__icontains=q) | Q(slug__icontains=q)
                     | Q(user__username__icontains=q) | Q(user__email__icontains=q))
             .select_related("user").order_by("display_name")[:PEOPLE_LIMIT])
    return JsonResponse({"people": [_person_row(person) for person in found]})


#: Holders drawn on the graph at most (each is a node): beyond it the graph
#: says how many were left out rather than drawing a hairball.
GRAPH_HOLDERS_LIMIT = 300


def _group_field(through, clearance_field):
    """The FK on a keeping table that is not the clearance: the group (a
    domain, a bucket)."""
    for field in through._meta.get_fields():
        if getattr(field, "many_to_one", False) and field is not clearance_field:
            return field
    return None


@login_required
@require_safe
def clearance_graph(request):
    """The clearances and what they keep, as a graph (JSON for the page's
    Graph view): a node per clearance, a node per group it keeps — whatever
    apps keep things to clearances, found by their PROTECT on the clearance
    (a map domain, a bucket), never by name — and, when asked
    (``?holders=1``), a node per holder. Superusers only."""
    if not may_manage(request.user):
        return JsonResponse({"error": _refusal(request.user)}, status=403)
    nodes, edges, kinds = [], [], []
    clearances = list(Clearance.objects.order_by("name")
                      .annotate(holder_count=Count("members", distinct=True)))
    for clearance in clearances:
        nodes.append({"id": f"clearance:{clearance.pk}", "label": clearance.name,
                      "kind": "clearance", "holders": clearance.holder_count})
    seen = set()
    for rel in _keeping_relations():
        through = rel.related_model
        group_field = _group_field(through, rel.field)
        if group_field is None:
            continue
        group_model = group_field.related_model
        kind = f"{group_model._meta.app_label}.{group_model._meta.model_name}"
        title = str(group_model._meta.verbose_name).capitalize()
        if kind not in {k["kind"] for k in kinds}:
            kinds.append({"kind": kind, "title": title})
        rows = list(through._default_manager.values_list(rel.field.attname, group_field.attname))
        groups = {g.pk: g for g in group_model._default_manager.filter(
            pk__in={gid for _cid, gid in rows})}
        for clearance_id, group_id in rows:
            group = groups.get(group_id)
            if group is None:
                continue
            node_id = f"{kind}:{group_id}"
            if node_id not in seen:
                seen.add(node_id)
                url = group.get_absolute_url() if hasattr(group, "get_absolute_url") else ""
                nodes.append({"id": node_id, "label": str(group), "kind": kind, "url": url})
            edges.append({"id": f"clearance:{clearance_id}->{node_id}",
                          "source": f"clearance:{clearance_id}", "target": node_id, "kind": "keeps"})
    left_out = 0
    if request.GET.get("holders") == "1":
        people = (Person.objects.filter(clearances__isnull=False).distinct()
                  .order_by("display_name", "pk"))
        total = people.count()
        left_out = max(0, total - GRAPH_HOLDERS_LIMIT)
        for person in people.prefetch_related("clearances")[:GRAPH_HOLDERS_LIMIT]:
            node_id = f"person:{person.pk}"
            nodes.append({"id": node_id, "label": person.display_name, "kind": "person"})
            for clearance in person.clearances.all():
                edges.append({"id": f"{node_id}->clearance:{clearance.pk}", "source": node_id,
                              "target": f"clearance:{clearance.pk}", "kind": "holds"})
    return JsonResponse({"nodes": nodes, "edges": edges, "kinds": kinds,
                         "holders_left_out": left_out})


@login_required
@require_safe
def clearance_targets(request):
    """Things of one kind a new clearance may keep, by a piece of their name —
    JSON for the New clearance modal (``?kind=<key>&q=``). The kinds are the
    apps' ``ClearanceTargetPlugin``s; an unknown kind is a 404."""
    if not may_manage(request.user):
        return JsonResponse({"error": _refusal(request.user)}, status=403)
    plugin = clearance_plugins.kind(request.GET.get("kind") or "")
    if plugin is None:
        return JsonResponse({"error": _("No such kind of thing.")}, status=404)
    q = " ".join((request.GET.get("q") or "").split())[:80]
    if not q:
        return JsonResponse({"results": []})
    return JsonResponse({"results": plugin.search(q, clearance_plugins.SEARCH_LIMIT)})


@login_required
@require_POST
def clearance_add(request):
    """Make a clearance with its speeds, its holders and what it clears, all
    or nothing."""
    if not may_manage(request.user):
        return _refused(request)
    name = " ".join((request.POST.get("name") or "").split())[:120]
    speeds, error = _read_speeds(request.POST)
    people = list(Person.objects.filter(pk__in=_ids(request.POST.getlist("person")),
                                        user__isnull=False)
                  .select_related("user").order_by("display_name"))
    targets = _read_targets(request.POST.getlist("target"))
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
                # Each app adds the clearance through its own door, with its
                # own audit record; a refusal undoes the whole clearance.
                for plugin, objects in targets.items():
                    plugin.keep(objects, clearance, actor=request.user)
        except ValidationError as exc:
            error = " ".join(exc.messages)
    if error:
        request.session[DRAFT_KEY] = {
            "open": True, "name": name, "error": error,
            "speeds": {pool: (request.POST.get(f"regen_{pool}") or "").strip()[:20]
                       for pool in REGEN_POOLS},
            "people": [_person_row(person) for person in people],
            "targets": _target_rows(targets),
        }
        return redirect("socialhub:clearances")
    kept = sum(len(objects) for objects in targets.values())
    if people:
        messages.success(request, _("Clearance %(name)s made, held by %(n)d.")
                         % {"name": name, "n": len(people)})
    else:
        messages.success(request, _("Clearance %(name)s made.") % {"name": name})
    if kept:
        messages.info(request, ngettext(
            "It keeps %(n)d group: what is in it is read only by its holders and superusers now.",
            "It keeps %(n)d groups: what is in them is read only by its holders and superusers now.",
            kept) % {"n": kept})
    return redirect("socialhub:clearances")


@login_required
@require_POST
def clearance_delete(request, pk):
    """A clearance goes — unless something still reads through it (an app's
    PROTECT), in which case the refusal says so and nothing changes."""
    from django.db.models import ProtectedError

    if not may_manage(request.user):
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
