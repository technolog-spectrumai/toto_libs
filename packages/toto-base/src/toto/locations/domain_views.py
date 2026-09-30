"""The Domains tab (2026-09-30): superusers group map items into map domains
and keep the domains to clearances.

A map domain (`MapDomain`) holds routes, map layers, addresses, zones,
territories — and whatever kind an installed app adds (the host's places) —
through typed through tables; the kinds are a plugin point
(`plugins/domain_plugins.py`, `MapDomainKind`). Clearances go on the domain,
never on an item: an item in no kept domain is every member's, an item in
kept domains is read by superusers and by whoever holds, for EVERY kept domain
of it, one of that domain's clearances (`access`).

The page is a paginated list — a table on a wide screen, cards on a narrow
one, like the socialhub Clearances tab — with these doors, all superusers':

* **New domain**, a modal: name, description, clearances and first items,
  found by kind and name (``domain_item_search``, JSON). Made in one
  transaction. A refusal is Post/Redirect/Get: what was typed and why it was
  refused go to the session, and the list re-opens the modal with them.
* **Items**, a modal: add items (kind + search) and take items out.
* **Clearances**, a modal: tick the domain's clearances.
* **Delete**, a confirm modal: the domain goes, its items stay (they are
  every member's again unless another kept domain holds them).

Every change is on the audit chain (``LOCATIONS.DOMAIN.*``). Works on a
GIS-off build too (`urls.GIS_FREE`): nothing here draws.
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Prefetch
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST, require_safe

from toto.ui import PageProcessor

from .access import may_manage_domains
from .models import MapDomain, MapDomainClearance
from .plugins.domain_plugins import MapDomainKind

logger = logging.getLogger("toto.locations")

#: Domains per page.
PER_PAGE = 10
#: Items of one kind a domain's Items modal lists before "+N more".
ITEMS_SHOWN = 100
#: Items one search answers with.
SEARCH_LIMIT = 20
#: Where a refused New domain waits for the list to draw it.
DRAFT_KEY = "locations.domain_draft"
NAME_MAX = 120
DESCRIPTION_MAX = 2000


def _refused(request):
    return HttpResponseForbidden(_("Map domains are managed by superusers."))


def _ids(values) -> set:
    """pks from a form: ASCII digits only, anything else ignored."""
    return {int(v) for v in values
            if isinstance(v, str) and v.isascii() and v.isdigit() and len(v) <= 18}


def _kinds() -> list:
    return MapDomainKind.all()


def _kind_json(kind) -> dict:
    return {"key": kind.get_key(), "title": str(kind.get_title()), "icon": kind.icon}


def _refs(values) -> dict:
    """``{kind key: {pk}}`` from ``kind:pk`` form values; an unknown kind or a
    junk pk is ignored."""
    refs = {}
    for value in values:
        key, _sep, pk = str(value).partition(":")
        if MapDomainKind.get(key) is None:
            continue
        pks = _ids([pk])
        if pks:
            refs.setdefault(key, set()).update(pks)
    return refs


def _resolve(values) -> list:
    """``[(kind, [objs])]`` for ``kind:pk`` values — only what exists."""
    resolved = []
    for key, pks in _refs(values).items():
        kind = MapDomainKind.get(key)
        objs = kind.resolve(pks)
        if objs:
            resolved.append((kind, objs))
    return resolved


def _clearances(values):
    from toto.socialhub.models import Clearance

    return list(Clearance.objects.filter(pk__in=_ids(values)).order_by("name"))


def _audit(action, obj, actor, **metadata):
    """One record on the audit chain (``LOCATIONS.DOMAIN.<ACTION>``) about the
    domain ``obj``; the chain never breaks a change."""
    from django.apps import apps

    if not apps.is_installed("toto.audit"):
        return
    from toto.audit.services import record

    try:
        with transaction.atomic():
            record(f"locations.domain.{action}", app_label="locations", obj=obj,
                   actor_user=actor, metadata=metadata)
    except Exception:  # noqa: BLE001
        logger.exception("audit: could not record domain.%s", action)


def set_domain_clearances(domain, clearances, *, actor):
    """Make ``clearances`` the domain's clearances — the one door (this page,
    and the socialhub's New clearance modal through the domain plugin), which
    records LOCATIONS.DOMAIN.CLEARANCES_CHANGED."""
    from toto.socialhub import clearance_access

    return clearance_access.set_clearances(
        domain, clearances, rows="clearance_rows", actor=actor,
        action="domain.clearances_changed", app_label="locations", domain=domain.name)


def _set_clearances(domain, clearances, actor):
    return set_domain_clearances(domain, clearances, actor=actor)


def _add_items(domain, resolved, actor) -> int:
    added = 0
    for kind, objs in resolved:
        for obj in kind.add(domain, objs):
            added += 1
            _audit("item_added", domain, actor, domain=domain.name, kind=kind.get_key(),
                   item=obj.pk, label=kind.label(obj))
    return added


def _remove_items(domain, resolved, actor) -> int:
    removed = 0
    for kind, objs in resolved:
        for obj in kind.remove(domain, objs):
            removed += 1
            _audit("item_removed", domain, actor, domain=domain.name, kind=kind.get_key(),
                   item=obj.pk, label=kind.label(obj))
    return removed


def _domain_json(domain, kinds, clearances) -> dict:
    items, more = {}, {}
    for kind in kinds:
        key = kind.get_key()
        count = kind.count(domain)
        items[key] = kind.listed(domain, ITEMS_SHOWN) if count else []
        more[key] = max(0, count - ITEMS_SHOWN)
    return {
        "pk": domain.pk,
        "name": domain.name,
        "items": items,
        "more": more,
        "clearances": [str(c.pk) for c in clearances],
        "items_url": reverse("locations:domain_items", args=[domain.pk]),
        "clearances_url": reverse("locations:domain_clearances", args=[domain.pk]),
        "delete_url": reverse("locations:domain_delete", args=[domain.pk]),
    }


def _page(request, *, draft=None):
    from toto.socialhub.models import Clearance

    kinds = _kinds()
    listing = MapDomain.objects.order_by("name").prefetch_related(Prefetch(
        "clearance_rows",
        queryset=MapDomainClearance.objects.select_related("clearance").order_by("clearance__name")))
    page = Paginator(listing, PER_PAGE).get_page(request.GET.get("page"))
    rows, data = [], {}
    for domain in page:
        clearances = [row.clearance for row in domain.clearance_rows.all()]
        counts = [{"key": kind.get_key(), "title": kind.get_title(), "icon": kind.icon,
                   "count": kind.count(domain)} for kind in kinds]
        rows.append({
            "domain": domain,
            "clearances": clearances,
            "counts": [c for c in counts if c["count"]],
            "total": sum(c["count"] for c in counts),
        })
        data[str(domain.pk)] = _domain_json(domain, kinds, clearances)
    return render(request, "locations/domains.html", PageProcessor().decorate({
        "rows": rows,
        "page_obj": page,
        "is_paginated": page.has_other_pages(),
        "extra_query": "",
        "total": MapDomain.objects.count(),
        "kinds": [_kind_json(kind) for kind in kinds],
        "all_clearances": Clearance.objects.order_by("name"),
        "domain_data": data,
        "draft": draft or {"open": False, "name": "", "description": "", "clearances": [],
                           "items": [], "error": ""},
        "search_url": reverse("locations:domain_item_search"),
    }, request))


@login_required
@require_safe
def domains(request):
    if not may_manage_domains(request.user):
        return _refused(request)
    return _page(request, draft=request.session.pop(DRAFT_KEY, None))


@login_required
@require_safe
def domain_item_search(request):
    """Items of one kind by a piece of their name — JSON for the pickers:
    ``?kind=route&q=coast`` -> ``{"items": [{pk, label, detail}]}``.
    Superusers read every item, so nothing here is gated further."""
    if not may_manage_domains(request.user):
        return JsonResponse({"error": _("Map domains are managed by superusers.")}, status=403)
    kind = MapDomainKind.get(request.GET.get("kind") or "")
    if kind is None:
        return JsonResponse({"error": _("No such kind of item."), "items": []}, status=400)
    q = " ".join((request.GET.get("q") or "").split())[:80]
    return JsonResponse({"kind": kind.get_key(), "items": kind.search(q, SEARCH_LIMIT)})


@login_required
@require_POST
def domain_add(request):
    """Make a domain with its clearances and first items, all or nothing."""
    if not may_manage_domains(request.user):
        return _refused(request)
    name = " ".join((request.POST.get("name") or "").split())
    description = (request.POST.get("description") or "").strip()
    clearances = _clearances(request.POST.getlist("clearance"))
    resolved = _resolve(request.POST.getlist("item"))
    error = ""
    if not name:
        error = _("A domain needs a name.")
    elif len(name) > NAME_MAX:
        error = _("A domain's name is at most %(n)d characters.") % {"n": NAME_MAX}
    elif len(description) > DESCRIPTION_MAX:
        error = _("A domain's description is at most %(n)d characters.") % {"n": DESCRIPTION_MAX}
    elif MapDomain.objects.filter(name__iexact=name).exists():
        error = _("There is already a domain called %(name)s.") % {"name": name}
    if error:
        request.session[DRAFT_KEY] = {
            "open": True, "name": name[:NAME_MAX], "description": description[:DESCRIPTION_MAX],
            "error": error,
            "clearances": [str(c.pk) for c in clearances],
            "items": [{"kind": kind.get_key(), "title": str(kind.get_title()), **kind.row(obj)}
                      for kind, objs in resolved for obj in objs],
        }
        return redirect("locations:domains")
    with transaction.atomic():
        domain = MapDomain.objects.create(name=name, description=description)
        _audit("created", domain, request.user, domain=name, description=description)
        if clearances:
            _set_clearances(domain, clearances, request.user)
        added = _add_items(domain, resolved, request.user)
    messages.success(request, _("Domain %(name)s made with %(n)d items.") % {"name": name, "n": added}
                     if added else _("Domain %(name)s made.") % {"name": name})
    return redirect("locations:domains")


@login_required
@require_POST
def domain_items(request, pk):
    """Put items in a domain (``add``) and take items out (``remove``), each
    a ``kind:pk``."""
    if not may_manage_domains(request.user):
        return _refused(request)
    domain = get_object_or_404(MapDomain, pk=pk)
    with transaction.atomic():
        removed = _remove_items(domain, _resolve(request.POST.getlist("remove")), request.user)
        added = _add_items(domain, _resolve(request.POST.getlist("add")), request.user)
    if added or removed:
        messages.success(request, _("%(name)s: %(added)d added, %(removed)d taken out.")
                         % {"name": domain.name, "added": added, "removed": removed})
    else:
        messages.info(request, _("Nothing changed."))
    return redirect(_back(request))


@login_required
@require_POST
def domain_clearances(request, pk):
    """The domain's clearances: none ticked opens its items to every member."""
    if not may_manage_domains(request.user):
        return _refused(request)
    domain = get_object_or_404(MapDomain, pk=pk)
    before, after = _set_clearances(domain, _clearances(request.POST.getlist("clearance")),
                                    request.user)
    if before != after:
        messages.success(request, _("Clearances of %(name)s saved.") % {"name": domain.name})
    else:
        messages.info(request, _("Nothing changed."))
    return redirect(_back(request))


@login_required
@require_POST
def domain_delete(request, pk):
    """The domain goes with its rows; its items and the clearances stay."""
    if not may_manage_domains(request.user):
        return _refused(request)
    domain = get_object_or_404(MapDomain, pk=pk)
    name = domain.name
    _audit("deleted", domain, request.user, domain=name,
           clearances=sorted(domain.clearance_rows.values_list("clearance__name", flat=True)),
           items={kind.get_key(): kind.count(domain) for kind in _kinds()})
    domain.delete()
    messages.success(request, _("Domain %(name)s removed.") % {"name": name})
    return redirect("locations:domains")


def _back(request):
    """Back to the list, on the page the form came from."""
    page = request.POST.get("page") or ""
    url = reverse("locations:domains")
    return f"{url}?page={page}" if page.isascii() and page.isdigit() and len(page) <= 6 else url
