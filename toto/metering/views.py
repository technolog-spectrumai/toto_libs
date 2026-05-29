from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor

from .forms import UsageEventForm, UsageMetricForm, UsageQuotaForm
from .models import EventStatus, UsageEvent, UsageMetric, UsageQuota


def metering_render(request, template_name, context):
    return render(request, template_name, PageProcessor().decorate(context, request))


# ---------------------------------------------------------------------------
# UsageMetric CRUD
# ---------------------------------------------------------------------------

@login_required
def metric_list(request):
    qs = UsageMetric.objects.all()
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(code__icontains=q) | qs.filter(name__icontains=q) | qs.filter(namespace__icontains=q)
    paginator = Paginator(qs.order_by("namespace", "code"), 30)
    page = paginator.get_page(request.GET.get("page"))
    return metering_render(request, "metering/metric_list.html", {
        "page_obj": page,
        "q": q,
    })


@login_required
def metric_create(request):
    if request.method == "POST":
        form = UsageMetricForm(request.POST)
        if form.is_valid():
            metric = form.save()
            messages.success(request, _("Metric '%(code)s' created.") % {"code": metric.code})
            return redirect("metering:metric_list")
    else:
        form = UsageMetricForm()
    return metering_render(request, "metering/metric_form.html", {
        "form": form, "action": _("Create Metric"),
    })


@login_required
def metric_update(request, pk):
    metric = get_object_or_404(UsageMetric, pk=pk)
    if request.method == "POST":
        form = UsageMetricForm(request.POST, instance=metric)
        if form.is_valid():
            form.save()
            messages.success(request, _("Metric '%(code)s' updated.") % {"code": metric.code})
            return redirect("metering:metric_list")
    else:
        form = UsageMetricForm(instance=metric)
    return metering_render(request, "metering/metric_form.html", {
        "form": form, "object": metric, "action": _("Edit Metric"),
    })


@login_required
def metric_delete(request, pk):
    metric = get_object_or_404(UsageMetric, pk=pk)
    if request.method == "POST":
        code = metric.code
        metric.delete()
        messages.success(request, _("Metric '%(code)s' deleted.") % {"code": code})
        return redirect("metering:metric_list")
    return metering_render(request, "metering/confirm_delete.html", {
        "object": metric,
        "object_name": metric.code,
        "cancel_url": "metering:metric_list",
    })


# ---------------------------------------------------------------------------
# UsageEvent CRUD
# ---------------------------------------------------------------------------

@login_required
def usage_list(request):
    from django.db.models import Q
    q = request.GET.get("q", "").strip()
    metric_filter = request.GET.get("metric", "").strip()
    status_filter = request.GET.get("status", "").strip()

    qs = UsageEvent.objects.select_related("metric")
    if q:
        qs = qs.filter(
            Q(subject_id__icontains=q) |
            Q(source_id__icontains=q) |
            Q(description__icontains=q) |
            Q(idempotency_key__icontains=q)
        )
    if metric_filter:
        qs = qs.filter(metric__code=metric_filter)
    if status_filter:
        qs = qs.filter(status=status_filter)

    paginator = Paginator(qs.order_by("-occurred_at"), 50)
    page = paginator.get_page(request.GET.get("page"))
    return metering_render(request, "metering/usage_list.html", {
        "page_obj": page,
        "q": q,
        "metric_filter": metric_filter,
        "status_filter": status_filter,
        "metrics": UsageMetric.objects.filter(is_active=True).order_by("code"),
        "status_choices": EventStatus.choices,
    })


@login_required
def usage_detail(request, uid):
    event = get_object_or_404(UsageEvent.objects.select_related("metric"), uid=uid)
    return metering_render(request, "metering/usage_detail.html", {"event": event})


@login_required
def usage_create(request):
    if request.method == "POST":
        form = UsageEventForm(request.POST)
        if form.is_valid():
            event = form.save()
            messages.success(request, _("Usage event recorded."))
            return redirect("metering:usage_detail", uid=event.uid)
    else:
        form = UsageEventForm()
    return metering_render(request, "metering/usage_form.html", {
        "form": form, "action": _("Record Usage Event"),
    })


@login_required
@require_POST
def usage_void(request, uid):
    event = get_object_or_404(UsageEvent, uid=uid)
    if event.status == EventStatus.VOIDED:
        messages.warning(request, _("Event is already voided."))
    else:
        from .services import void_usage_event
        reason = request.POST.get("reason", "")
        void_usage_event(event, reason=reason, actor=request.user)
        messages.success(request, _("Event voided."))
    return redirect("metering:usage_detail", uid=uid)


# ---------------------------------------------------------------------------
# UsageQuota CRUD
# ---------------------------------------------------------------------------

@login_required
def quota_list(request):
    qs = UsageQuota.objects.select_related("metric")
    q = request.GET.get("q", "").strip()
    if q:
        from django.db.models import Q
        qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q) | Q(subject_id__icontains=q))
    paginator = Paginator(qs.order_by("metric__code", "code"), 30)
    page = paginator.get_page(request.GET.get("page"))
    return metering_render(request, "metering/quota_list.html", {
        "page_obj": page, "q": q,
    })


@login_required
def quota_create(request):
    if request.method == "POST":
        form = UsageQuotaForm(request.POST)
        if form.is_valid():
            quota = form.save()
            messages.success(request, _("Quota '%(code)s' created.") % {"code": quota.code})
            return redirect("metering:quota_list")
    else:
        form = UsageQuotaForm()
    return metering_render(request, "metering/quota_form.html", {
        "form": form, "action": _("Create Quota"),
    })


@login_required
def quota_update(request, pk):
    quota = get_object_or_404(UsageQuota, pk=pk)
    if request.method == "POST":
        form = UsageQuotaForm(request.POST, instance=quota)
        if form.is_valid():
            form.save()
            messages.success(request, _("Quota '%(code)s' updated.") % {"code": quota.code})
            return redirect("metering:quota_list")
    else:
        form = UsageQuotaForm(instance=quota)
    return metering_render(request, "metering/quota_form.html", {
        "form": form, "object": quota, "action": _("Edit Quota"),
    })


@login_required
def quota_delete(request, pk):
    quota = get_object_or_404(UsageQuota, pk=pk)
    if request.method == "POST":
        code = quota.code
        quota.delete()
        messages.success(request, _("Quota '%(code)s' deleted.") % {"code": code})
        return redirect("metering:quota_list")
    return metering_render(request, "metering/confirm_delete.html", {
        "object": quota,
        "object_name": quota.code,
        "cancel_url": "metering:quota_list",
    })


# ---------------------------------------------------------------------------
# Metrics dashboard
# ---------------------------------------------------------------------------

@login_required
def metrics(request):
    from .services import metering_metrics
    data = metering_metrics()
    return metering_render(request, "metering/metrics.html", {
        "daily_series_json": json.dumps(data["daily_series"]),
        "by_status_json": json.dumps(data["by_status"]),
        "by_metric_json": json.dumps(
            [{"code": d["metric__code"], "count": d["count"]} for d in data["by_metric"]]
        ),
        **data,
    })


# ---------------------------------------------------------------------------
# API: record usage
# ---------------------------------------------------------------------------

@csrf_exempt
@require_POST
def api_record_usage(request):
    """
    POST /metering/api/record/
    JSON body: metric_code, quantity, [unit, subject_type, subject_id, ...]
    Returns: {uid, metric, quantity, status} or {error}
    """
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    metric_code = body.get("metric_code", "")
    if not metric_code:
        return JsonResponse({"error": "metric_code is required."}, status=400)

    try:
        quantity = body["quantity"]
    except KeyError:
        return JsonResponse({"error": "quantity is required."}, status=400)

    from .services import QuotaExceeded, record_usage
    from django.core.exceptions import ValidationError

    try:
        event = record_usage(
            metric_code=metric_code,
            quantity=quantity,
            unit=body.get("unit", ""),
            occurred_at=None,
            source_type=body.get("source_type", ""),
            source_id=body.get("source_id", ""),
            source_label=body.get("source_label", ""),
            subject_type=body.get("subject_type", ""),
            subject_id=body.get("subject_id", ""),
            subject_label=body.get("subject_label", ""),
            idempotency_key=body.get("idempotency_key", ""),
            description=body.get("description", ""),
            metadata=body.get("metadata") or {},
            enforce_quota=body.get("enforce_quota", True),
            create_metric=body.get("create_metric", False),
        )
    except QuotaExceeded as exc:
        return JsonResponse(
            {
                "error": "quota_exceeded",
                "detail": str(exc),
                "quota_code": exc.decision.quota_code,
                "used": str(exc.decision.used_quantity),
                "limit": str(exc.decision.limit_quantity),
            },
            status=429,
        )
    except ValidationError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    return JsonResponse(
        {
            "uid": str(event.uid),
            "metric": event.metric.code,
            "quantity": str(event.quantity),
            "unit": event.unit,
            "status": event.status,
            "occurred_at": event.occurred_at.isoformat(),
        },
        status=201,
    )
