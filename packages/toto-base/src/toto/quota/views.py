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

from decimal import Decimal, InvalidOperation

from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from toto.ui import PageProcessor

from . import rates
from .api import get_policy, remaining, used
from .choices import Mode
from .forms import policy_form_for
from .metrics import policy_model_for, registry


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _staff_only(request):
    if not request.user.is_staff:
        raise PermissionDenied


def _billing_url(code: str) -> str:
    """Where this metric's price is set, or "" when nothing bills.

    Prefers the metric's own price row over the rate-card index — landing on a
    list of every tariff when you asked about one metric was never useful. Falls
    back to the list while a metric is still free and so has no row to edit.

    The URL arrives as a pre-computed string from :mod:`toto.quota.rates` rather
    than being reversed here, so no quota template ever names ``tariffs:``.
    """
    if not apps.is_installed("toto.tariffs"):
        return ""
    if code:
        specific = rates.advanced_url(code)
        if specific:
            return specific
    try:
        return reverse("tariffs:tariff_list")
    except Exception:
        return ""


def _tax_rules_url() -> str:
    """Where levy allowances are edited, or "" when no levy engine ships."""
    if not apps.is_installed("toto.tax"):
        return ""
    try:
        return reverse("tax:rules")
    except Exception:
        return ""


def _row(metric, user, *, prices=None, spend=None):
    """One line of the limits table: what it is, its cap, and your use of it.

    ``prices`` and ``spend`` are the whole rate card and the whole spend summary,
    passed in so a page renders them with one query each rather than one per
    row. Both are ``{}`` on a host with no economy, and every price key then
    comes back None — which is the same thing a free metric produces, and
    deliberately so.
    """
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

    # Clamp for the bar but keep "over" as its own fact, so the bar never
    # overflows its track while still being able to turn red.
    price = (prices or {}).get(metric.code)
    return {
        "metric": metric,
        "policy": policy,
        "limit": limit,
        "mode": policy.mode if policy else None,
        "period": policy.period if policy else metric.period,
        "used": consumed,
        "remaining": left,
        "pct_used": pct,
        "pct_bar": min(int(pct), 100) if pct is not None else 0,
        "over": bool(pct is not None and pct > 100),
        "has_table": policy_model is not None,
        "price": price,
        "spent": (spend or {}).get(metric.code),
        "advanced_url": price["advanced_url"] if price else "",
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
def rate_desk(request):
    """Every limit and every price on one screen, saved in one POST.

    Limits and prices live in different apps and used to be set one metric at a
    time on two different pages, which made "what does this platform actually
    cap, and what does it charge for it" a question nobody could answer without
    clicking through everything.
    """
    _staff_only(request)

    if request.method == "POST":
        plan, errors = _parse_desk(request.POST)
        if errors:
            # Nothing is written until every row is good: a ValidationError
            # raised mid-loop would roll back but still render as success, and
            # leave the operator guessing which row was at fault.
            for message in errors:
                messages.error(request, message)
        else:
            with transaction.atomic():
                _apply_desk(plan)
            messages.success(request, _("Limits and prices saved."))
            return redirect("quota:rate_desk")

    prices = rates.rate_card()
    groups = []
    for app_label, metrics in registry.by_app().items():
        rows = [_row(m, None, prices=prices) for m in metrics]
        config = apps.get_app_config(app_label) if apps.is_installed(f"toto.{app_label}") else None
        groups.append({
            "app_label": app_label,
            "verbose_name": getattr(config, "verbose_name", app_label) if config else app_label,
            "rows": rows,
        })

    pricing = rates.pricing_enabled()
    return _render(request, "quota/rate_desk.html", {
        "groups": groups,
        "metric_count": len(registry),
        "pricing_enabled": pricing,
        # Where the recurring levies' free allowances are set. A pre-computed
        # string like _billing_url, so no quota template ever names "tax:" —
        # empty on the hosts that ship no levy engine.
        "tax_rules_url": _tax_rules_url(),
        "price_asset": rates.price_asset_symbol(),
        # Currencies a price may be denominated in. Empty on a host with no
        # assets app, and the grid then renders no picker — the price column
        # behaves exactly as it did before per-metric currencies existed.
        "billing_assets": rates.billing_assets() if pricing else [],
        # The <th> and every <td> read this one flag, so a price column can
        # never appear as a header with no cells under it.
        "column_count": 6 if pricing else 4,
        "modes": Mode.choices,
    })


def _parse_desk(post):
    """Read the whole grid before writing any of it.

    The field-naming rule, which inverts formica's on purpose:

    ==================  ==========================================
    key absent          the row was not rendered — leave it alone
    key present, blank  explicit erasure: drop the policy / price
    key present, valued upsert
    ==================  ==========================================

    Absent-versus-blank is what stops a half-rendered form, a browser that
    dropped fields, or a host where tariffs vanished between GET and POST from
    silently wiping the rate card. Codes are dotted, so `__` separates the
    prefix from the code.
    """
    plan, errors = [], []
    pricing = rates.pricing_enabled()

    for metric in registry.installed():
        entry = {"metric": metric}
        limit_key = f"limit__{metric.code}"
        price_key = f"price__{metric.code}"

        if limit_key in post:
            raw = (post.get(limit_key) or "").strip()
            if raw == "":
                entry["limit"] = None
            else:
                try:
                    value = Decimal(raw)
                except (InvalidOperation, ValueError):
                    errors.append(_("%(code)s: %(value)r is not a number.")
                                  % {"code": metric.code, "value": raw})
                    continue
                if value < 0:
                    errors.append(_("%(code)s: a limit cannot be negative.")
                                  % {"code": metric.code})
                    continue
                entry["limit"] = value
            entry["mode"] = post.get(f"mode__{metric.code}") or Mode.BLOCK

        # Guard one: never even look at a price field on an unbilled host.
        if pricing and price_key in post:
            try:
                # Guard two: parse here so a bad number is a form error rather
                # than an exception out of the write phase.
                from toto.tariffs.rate_card import parse_price

                entry["price"] = parse_price(post.get(price_key))
                entry["has_price"] = True
                # Per-metric currency. Absent or blank keeps the inherited one
                # (tariff default, else the host gas asset), so a desk that does
                # not render the column behaves exactly as before.
                entry["asset_id"] = (post.get(f"asset__{metric.code}") or "").strip() or None
            except ImportError:  # pragma: no cover
                pass
            except ValueError as exc:
                errors.append(f"{metric.code}: {exc}")
                continue

        if len(entry) > 1:
            plan.append(entry)

    return plan, errors


def _apply_desk(plan):
    """Write a validated plan. Caller owns the transaction."""
    for entry in plan:
        metric = entry["metric"]
        if "limit" in entry:
            policy_model = policy_model_for(metric.app_label)
            if policy_model is not None:
                _set_default_limit(policy_model, metric, entry["limit"], entry.get("mode"))
        if entry.get("has_price"):
            # Guard three: set_price is itself a no-op when nothing bills.
            if entry["price"] is None:
                rates.clear_price(metric.code)
            else:
                rates.set_price(metric.code, entry["price"], asset_id=entry.get("asset_id"))


def _set_default_limit(policy_model, metric, limit, mode=None):
    """Set or drop the everyone-policy for one metric.

    A blank limit deletes the row rather than storing zero — nothing is limited
    until a policy exists, so an absent policy is how "unlimited" is spelled.
    """
    existing = policy_model.objects.filter(metric_code=metric.code, user__isnull=True).first()
    if limit is None:
        if existing is not None:
            existing.delete()
        return
    if existing is None:
        existing = policy_model(metric_code=metric.code, user=None,
                                name=metric.label, unit=metric.unit,
                                period=metric.period)
    existing.limit = limit
    if mode:
        existing.mode = mode
    existing.active = True
    existing.save()


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
def my_usage(request, app_label=None):
    """What the signed-in user has consumed, what it cost, and what is left.

    ``app_label`` narrows it to one app, which is where each metered app's own
    "Usage" link lands.
    """
    if app_label is not None and not registry.for_app(app_label):
        # A tab pointing at an app that meters nothing would be a dead link;
        # 404 rather than render a convincingly empty page.
        raise Http404(f"{app_label!r} meters nothing on this host.")

    metrics = registry.for_app(app_label) if app_label else registry.all()
    prices = rates.rate_card()
    spend = rates.spend_by_metric(request.user)
    rows = [r for r in (_row(m, request.user, prices=prices, spend=spend)
                        for m in metrics) if r["has_table"]]

    at_limit = sum(1 for r in rows if r["over"] or (r["limit"] and r["remaining"] == 0))
    actions = sum((r["used"] or 0) for r in rows)

    return _render(request, "quota/my_usage.html", {
        "rows": rows,
        "scope_app": app_label,
        "billing_enabled": apps.is_installed("toto.tariffs"),
        "price_asset": rates.price_asset_symbol(),
        "balance": rates.balance_of(request.user),
        "wallet_url": rates.wallet_url(),
        "kpi_actions": actions,
        "kpi_at_limit": at_limit,
        "kpi_spent": _total_spend(spend),
        # Labels come from here rather than the template: `{% include with %}`
        # cannot call gettext, and untranslated KPI captions beside translated
        # ones read as a bug.
        "kpi_actions_label": _("Actions this period"),
        "kpi_at_limit_label": _("At their limit"),
        "kpi_at_limit_tone": "warn" if at_limit else "success",
        "kpi_balance_label": _("Gas balance"),
        "kpi_spent_label": _("Spent this period"),
    })


@login_required
def fees(request):
    """Where the platform's money comes from — two renderings, one URL.

    Staff see income by source, the split as a pie, and every price that is
    denominated in something this platform is not contracted for. A user sees
    what things cost *them* and what they personally owe.

    Deliberately NOT behind ``_staff_only``. The tab exists for everyone,
    because "why was I charged" is a fair question and the answer is not
    sensitive; what differs is the content, not the permission. Every editor
    this page links to keeps its own independent 403, so nothing here widens
    access to anything.
    """
    from . import feeboard

    is_staff = request.user.is_staff
    context = {
        "is_staff_view": is_staff,
        "billing_enabled": apps.is_installed("toto.tariffs"),
        "price_asset": rates.price_asset_symbol(),
        "wallet_url": rates.wallet_url(),
    }

    if is_staff:
        board = feeboard.income_board()
        context.update({
            "board": board,
            "income_pie_json": feeboard.income_pie_json(board),
        })
    else:
        # What it costs me, and what I owe — no platform totals, no editors.
        levy_rows, community_rows = _my_levy_rows(request.user)
        context.update({
            "rate_card": sorted(rates.rate_card().items()),
            "balance": rates.balance_of(request.user),
            "levy_rows": levy_rows,
            "community_rows": community_rows,
        })

    return _render(request, "quota/fees.html", context)


def _my_levy_rows(user):
    """(per-metric levies, community-fee rows) — ([], []) where tax is absent.

    Two lists rather than one, because the two estimators return different
    shapes and always have: a levy row is keyed on a rule and a metric, a
    community-fee row on an asset. tax/views.py::my_levies keeps them apart for
    the same reason, and flattening them here would only move the branch into
    the template.

    is_installed BEFORE the import: a host can pin the economy wheel without
    installing toto.tax — placidia does — and importing its models there raises
    RuntimeError out of Django's model metaclass, which `except ImportError`
    never catches.
    """
    if not apps.is_installed("toto.tax"):
        return [], []
    try:
        from toto.tax import services as tax_services
        from toto.tax import surplus as tax_surplus
    except ImportError:
        return [], []

    levies, community = [], []
    try:
        levies = tax_services.estimate_for_user(user) or []
    except Exception:  # noqa: BLE001 - an estimate must not break the page
        pass
    try:
        community = tax_surplus.estimate_for_user(user) or []
    except Exception:  # noqa: BLE001
        pass
    return levies, community


def _total_spend(spend):
    """Everything spent this period, when it is all in one asset.

    Prices can name any asset, so a single total is only honest when there is a
    single denominator — there is no exchange rate anywhere in the ledger and
    inventing one for a KPI card would be the worst place to start.
    """
    assets = {row["asset"] for row in spend.values()}
    if len(assets) != 1:
        return None
    return {"amount": sum(row["amount"] for row in spend.values()),
            "asset": assets.pop()}
