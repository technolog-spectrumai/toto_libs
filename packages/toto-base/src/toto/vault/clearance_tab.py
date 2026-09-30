"""The vault's **Clearances** tab (``clearances/``, 2026-09-30): every bucket,
the clearances keeping it and, per clearance, who holds it.

Superuser functionality, so the plan system's own answer — a real superuser
ON THE SUPERUSER PLAN (``plan_gate.superuser_plan_door``; the account alone is
refused, 403; a visitor who is not signed in goes to the login page), and the
tab in ``vault/base.html`` shows to the same people
(``vault_flags.superuser_plan``).

Read-only. A bucket's clearances are SET on the bucket's own page
(``metrics/<slug>/#clearances``, ``clearances.bucket_clearances``), which each
row links to; the clearances themselves — who holds them — are made in the
socialhub's Clearances tab, which this page links to as well. The rule the page
describes is ``clearances.py``'s: a bucket kept to clearances has its files read
by superusers and by holders of one of them; a bucket kept by none is OPEN — its
files follow the vault's usual rule (owner, public flag, bucket owner, folder).

Every bucket is listed, personal ones and those being deleted included,
paginated, with a filter (all / kept / open) the counters double as. A page
costs the same few queries however many buckets, clearances and holders it
shows (``bucket_rows``): the page's buckets with owner, peer and provider
joined (the Remote badge reads the last two), their clearance rows with the
clearance joined, and every holder of those clearances with person and user.
"""

from __future__ import annotations

from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_safe

from toto.ui import PageProcessor

from .models import Bucket, BucketClearance
from .plan_gate import superuser_plan_door

#: Buckets per page.
PER_PAGE = 25
#: Holder names shown per clearance before "+N more" — a clearance everyone in
#: the company holds would otherwise print the staff list once per bucket.
HOLDERS_SHOWN = 20
#: The filter, as ``?show=`` takes it. The first is the default.
SHOWS = ("all", "kept", "open")


def buckets_for(show: str):
    """Every bucket, or only those kept to clearances, or only the open ones —
    by name, so a page never reorders under the reader."""
    kept = BucketClearance.objects.values("bucket_id")
    buckets = Bucket.objects.all()
    if show == "kept":
        buckets = buckets.filter(pk__in=kept)
    elif show == "open":
        buckets = buckets.exclude(pk__in=kept)
    return buckets.order_by("name", "pk")


def bucket_rows(buckets) -> list:
    """For each bucket: the clearances keeping it, by name, and per clearance
    the display names of the people holding it (sorted; at most
    ``HOLDERS_SHOWN``, ``more`` counting the rest; [] = nobody holds it).
    ``open`` when no clearance keeps the bucket. Three queries, whatever the
    count: the buckets, their clearance rows, the holders."""
    from toto.people.models import Person

    buckets = list(buckets.select_related("owner", "peer", "provider").prefetch_related(Prefetch(
        "clearance_rows",
        queryset=BucketClearance.objects.select_related("clearance").order_by("clearance__name"))))
    ids = {r.clearance_id for b in buckets for r in b.clearance_rows.all()}
    holders: dict[int, list[str]] = {}
    if ids:
        through = Person.clearances.through
        for link in through.objects.filter(clearance_id__in=ids).select_related("person__user"):
            holders.setdefault(link.clearance_id, []).append(link.person.full_name)
    rows = []
    for bucket in buckets:
        keeps = []
        for row in bucket.clearance_rows.all():
            names = sorted(holders.get(row.clearance_id, []), key=str.casefold)
            keeps.append({"clearance": row.clearance, "holders": names[:HOLDERS_SHOWN],
                          "count": len(names), "more": max(len(names) - HOLDERS_SHOWN, 0)})
        rows.append({
            "bucket": bucket,
            "open": not keeps,
            "keeps": keeps,
            "url": reverse("vault:bucket_metrics", args=[bucket.slug]) + "#clearances",
        })
    return rows


@superuser_plan_door
@require_safe
def clearances_tab(request):
    show = request.GET.get("show", "")
    if show not in SHOWS:
        show = SHOWS[0]
    total = Bucket.objects.count()
    kept = BucketClearance.objects.values("bucket_id").distinct().count()
    page_obj = Paginator(buckets_for(show), PER_PAGE).get_page(request.GET.get("page"))
    try:
        socialhub_clearances_url = reverse("socialhub:clearances")
    except NoReverseMatch:
        socialhub_clearances_url = ""
    context = {
        "active_tab": "clearances",
        "rows": bucket_rows(page_obj.object_list),
        "page_obj": page_obj,
        "is_paginated": page_obj.has_other_pages(),
        "extra_query": "" if show == SHOWS[0] else f"&show={show}",
        "show": show,
        "filters": [("all", _("Buckets"), total),
                    ("kept", _("Kept to clearances"), kept),
                    ("open", _("Kept by no clearance"), total - kept)],
        "socialhub_clearances_url": socialhub_clearances_url,
    }
    return render(request, "vault/clearances_tab.html", PageProcessor().decorate(context, request))
