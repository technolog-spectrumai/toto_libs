"""Reading the audit trail, and verifying it.

Staff-only. An audit trail lists who touched what and when, which is a
description of the company's internal activity; it is not a general-readership
page and is not offered as one.
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from toto.audit.models import AuditRecord
from toto.audit.services import verify_chain
from toto.ui import PageProcessor

#: Rows per page. A module constant, the way every other paginated page on the
#: platform states it (antivirus FINDINGS_CAP, assets PAGE_SIZE, memo PER_PAGE)
#: — not a Django setting, which would be a knob nobody asked for.
PER_PAGE = 50


@staff_member_required
def index(request):
    rows = AuditRecord.objects.select_related("actor_user")

    action = request.GET.get("action", "").strip().upper()
    app_label = request.GET.get("app", "").strip()
    query = request.GET.get("q", "").strip()
    if action:
        rows = rows.filter(action=action)
    if app_label:
        rows = rows.filter(app_label=app_label)
    if query:
        rows = rows.filter(
            Q(object_description__icontains=query)
            | Q(actor_username__icontains=query)
            | Q(object_id=query)
        )

    page = Paginator(rows, PER_PAGE).get_page(request.GET.get("page"))

    # The filter querystring, for oya/partials/_server_pagination.html. Two
    # things about its contract are load-bearing: it is `&`-prefixed, because
    # the partial writes `?page=N{{ extra_query }}` itself; and it must NOT
    # carry `page`, or paging would emit `?page=2&page=5` and Django's
    # request.GET.get("page") takes the first — a silent off-by-N.
    # Without it, page 2 of a filtered search silently showed unfiltered rows.
    encoded = urlencode({
        key: value for key, value in (
            ("action", action), ("app", app_label), ("q", query),
        ) if value
    })

    # PageProcessor, like every page that extends the oya chrome: the base
    # template resolves `platform` and the theme context, and on a host whose
    # chrome passes it through a filter, its absence is a 500, not a blank.
    # The truth book's own chrome forgave the omission, which is how these
    # three views arrived here without it.
    return render(request, "audit/index.html", PageProcessor().decorate({
        "page_obj": page,
        "is_paginated": page.has_other_pages(),
        "extra_query": ("&" + encoded) if encoded else "",
        "actions": (AuditRecord.objects.values_list("action", flat=True)
                    .distinct().order_by("action")),
        "apps": (AuditRecord.objects.values_list("app_label", flat=True)
                 .distinct().order_by("app_label")),
        "filters": {"action": action, "app": app_label, "q": query},
        # The UNFILTERED count. It doubles as the empty-state discriminator:
        # an empty page with `total` above zero means the filter matched
        # nothing, which is a different sentence from "nothing recorded yet".
        "total": AuditRecord.objects.count(),
    }, request))


@staff_member_required
def detail(request, pk):
    record = get_object_or_404(AuditRecord.objects.select_related("actor_user", "chain"), pk=pk)
    return render(request, "audit/detail.html",
                  PageProcessor().decorate({"record": record}, request))


@staff_member_required
def verify(request):
    """Walk the whole chain and report. Read-only; safe to run at any time."""
    result = verify_chain()
    return render(request, "audit/verify.html",
                  PageProcessor().decorate({"result": result}, request))
