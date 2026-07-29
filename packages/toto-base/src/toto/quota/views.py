"""Screens for reading and setting limits.

This is the limits half of metering. What things *cost* lives in the host's
billing app, which this one cannot import — so where a price would go, these
pages link out to it, and only when it exists. A host with no ledger gets the
same screens minus that column.

Everything listed comes from the metric registry rather than the database, so a
metric appears here the moment an app declares it, before anyone has used it or
set a limit on it.
"""

from __future__ import annotations

from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from toto.ui import PageProcessor

from .api import get_policy, remaining, used
from .forms import policy_form_for
from .metrics import policy_model_for, registry


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _staff_only(request):
    if not request.user.is_staff:
        raise PermissionDenied


def _billing_url(code: str) -> str:
    """Where this metric's price is set, or "" when nothing bills.

    Asks the app registry rather than importing anything — the billing app is
    owned by one host and this module ships to all of them.
    """
    if not apps.is_installed("toto.tariffs"):
        return ""
    try:
        return reverse("tariffs:tariff_list")
    except Exception:
        return ""


def _row(metric, user):
    """One line of the limits table: what it is, its cap, and your use of it."""
    policy_model = policy_model_for(metric.app_label)
    policy = get_policy(policy_model, metric.code, user) if policy_model else None

    consumed = left = None
    if policy_model is not None:
        period = policy.period if policy else metric.period
        consumed = used(policy_model, metric.code, user, period=period)
        left = remaining(policy_model, metric.code, user)

    limit = policy.limit if policy else None
    pct = None
    if limit:
        pct = min(float(consumed / limit * 100), 999) if consumed is not None else None

    return {
        "metric": metric,
        "policy": policy,
        "limit": limit,
        "mode": policy.mode if policy else None,
        "period": policy.period if policy else metric.period,
        "used": consumed,
        "remaining": left,
        "pct_used": pct,
        "has_table": policy_model is not None,
    }


@login_required
def index(request):
    """Every metric this platform can meter, with your usage against it."""
    groups = []
    for app_label, metrics in registry.by_app().items():
        rows = [_row(m, request.user) for m in metrics]
        config = apps.get_app_config(app_label) if apps.is_installed(f"toto.{app_label}") else None
        groups.append({
            "app_label": app_label,
            "verbose_name": getattr(config, "verbose_name", app_label) if config else app_label,
            "rows": rows,
        })

    return _render(request, "quota/index.html", {
        "groups": groups,
        "metric_count": len(registry),
        "is_staff": request.user.is_staff,
        "billing_url": _billing_url(""),
        "billing_enabled": apps.is_installed("toto.tariffs"),
    })


@login_required
def metric_detail(request, code):
    """Set the default limit for one metric, and manage per-user overrides."""
    _staff_only(request)

    metric = registry.get(code)
    if metric is None:
        raise Http404(f"No metric registered as {code!r}.")

    policy_model = policy_model_for(metric.app_label)
    if policy_model is None:
        raise Http404(
            f"{metric.app_label} declares {code!r} but ships no quota table to store limits in."
        )

    default = policy_model.objects.filter(metric_code=code, user__isnull=True).first()
    form_class = policy_form_for(policy_model)

    if request.method == "POST" and request.POST.get("action") == "default":
        form = form_class(request.POST, instance=default)
        if form.is_valid():
            policy = form.save(commit=False)
            policy.metric_code = code
            policy.user = None
            if not policy.name:
                policy.name = metric.label
            policy.save()
            messages.success(request, _("Default limit saved."))
            return redirect("quota:metric_detail", code=code)
    else:
        form = form_class(
            instance=default,
            initial=None if default else {
                "limit": metric.default_limit,
                "unit": metric.unit,
                "period": metric.period,
                "active": True,
            },
        )

    override_form = policy_form_for(policy_model, include_user=True)(
        initial={"unit": metric.unit, "period": metric.period, "active": True}
    )
    if request.method == "POST" and request.POST.get("action") == "override":
        override_form = policy_form_for(policy_model, include_user=True)(request.POST)
        if override_form.is_valid():
            policy = override_form.save(commit=False)
            policy.metric_code = code
            if not policy.name:
                policy.name = f"{metric.label} — {policy.user}"
            policy.save()
            messages.success(request, _("Override saved for %(user)s.") % {"user": policy.user})
            return redirect("quota:metric_detail", code=code)

    overrides = (
        policy_model.objects.filter(metric_code=code, user__isnull=False)
        .select_related("user")
        .order_by("user__username")
    )

    return _render(request, "quota/metric_detail.html", {
        "metric": metric,
        # Templates cannot read _meta, so hand over the one bit they show.
        "policy_table": policy_model._meta.db_table,
        "default": default,
        "form": form,
        "override_form": override_form,
        "overrides": overrides,
        "billing_url": _billing_url(code),
        "billing_enabled": apps.is_installed("toto.tariffs"),
    })


@login_required
def override_delete(request, code, pk):
    """Drop a per-user override so the default applies again."""
    _staff_only(request)
    if request.method != "POST":
        return redirect("quota:metric_detail", code=code)

    metric = registry.get(code)
    policy_model = policy_model_for(metric.app_label) if metric else None
    if policy_model is None:
        raise Http404

    policy = get_object_or_404(policy_model, pk=pk, metric_code=code, user__isnull=False)
    who = policy.user
    policy.delete()
    messages.success(request, _("Removed the override for %(user)s.") % {"user": who})
    return redirect("quota:metric_detail", code=code)


@login_required
def my_usage(request):
    """What the signed-in user has consumed, across every metered app."""
    rows = [_row(m, request.user) for m in registry.all()]
    return _render(request, "quota/my_usage.html", {
        "rows": [r for r in rows if r["has_table"]],
        "billing_enabled": apps.is_installed("toto.tariffs"),
    })
