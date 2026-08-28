"""Reading the audit trail, and verifying it.

Superuser-only. An audit trail lists who touched what and when, which is a
description of the company's internal activity; it is not a general-readership
page and is not offered as one.
"""

from __future__ import annotations

from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, render

from toto.audit.models import AuditRecord
from toto.audit.services import verify_chain


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

    page = Paginator(rows, 50).get_page(request.GET.get("page"))
    return render(request, "audit/index.html", {
        "page": page,
        "actions": (AuditRecord.objects.values_list("action", flat=True)
                    .distinct().order_by("action")),
        "apps": (AuditRecord.objects.values_list("app_label", flat=True)
                 .distinct().order_by("app_label")),
        "filters": {"action": action, "app": app_label, "q": query},
        "total": AuditRecord.objects.count(),
    })


@staff_member_required
def detail(request, pk):
    record = get_object_or_404(AuditRecord.objects.select_related("actor_user", "chain"), pk=pk)
    return render(request, "audit/detail.html", {"record": record})


@staff_member_required
def verify(request):
    """Walk the whole chain and report. Read-only; safe to run at any time."""
    result = verify_chain()
    return render(request, "audit/verify.html", {"result": result})
