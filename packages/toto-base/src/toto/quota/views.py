"""Metering, organised by the thing being metered.

This app used to grow one screen per model: a Limits page, a Rate desk, a
Prices page, a Metrics page, an Allowances page, a Levies page. Six screens,
one key — every one of them keyed by metric code — so answering "what does a
document export cost, what is the cap, and how much have I got left" meant
visiting five of them and holding the answer in your head.

The axis here is the **metered thing**. There is a collection view of all of
them (:func:`index`) and a detail view of one (:func:`metric_detail`), and
every knob that thing has — its limit, its price, whether it is armed, its time
dials — is edited on the thing, in place. The other two
screens are a read-only explanation of the charging *kinds* (:func:`taxes`) and
the platform's income (:func:`fees`), which are genuinely different objects.

Both audiences share the same URLs. A member sees their usage, the cap and the
price; a staff member sees the same plus the editors, inline. Hiding is
cosmetic — every write path re-checks ``is_staff``, so a hand-posted field is
still refused.

What things *cost* lives in the host's billing app, which this one cannot
import. Three façades cross that boundary with plain data only:
:mod:`~toto.quota.rates` (prices), :mod:`~toto.quota.levies` (levies)
and :mod:`~toto.quota.times` (time dials). On a host with no economy every one
of them answers empty, and the money columns are then **absent rather than
blank** — the question does not exist there, so the page does not ask it.
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

from . import levies, rates, times
from .api import get_policy, remaining, used
from .choices import Mode
from .forms import policy_form_for
from .metrics import policy_model_for, registry


def _render(request, template, context):
    return render(request, template, PageProcessor().decorate(context, request))


def _staff_only(request):
    """The operator gate. `is_staff OR is_superuser`, like every other gate here.

    It used to test bare `is_staff`, which made this — the one desk that sets
    what things cost — the single place in the platform that refused a
    superuser. Django's two flags are independent and `is_superuser` does not
    imply `is_staff`, a point half a dozen modules in this tree carry a comment
    about; this one had the comment's lesson and not its code.
    """
    if not (request.user.is_staff or request.user.is_superuser):
        raise PermissionDenied


def _app_config(app_label):
    """The app config for a metric's owner, or None when it is not installed.

    Asks the registry by LABEL rather than testing ``is_installed("toto.<label>")``.
    The old prefix test disagreed with ``registry.installed()`` — which is what
    the POST path uses — so the page rendered editable rows for uninstalled apps
    that saving then silently ignored. It also lost the verbose_name of any app
    not namespaced under ``toto.``.
    """
    try:
        return apps.get_app_config(app_label)
    except LookupError:
        return None


def _advanced_url(code: str) -> str:
    """The tariff item's own editor, for the fields a form cannot express.

    Demoted to one "Advanced" link on the thing's page: the multi-rate-card CRUD
    still works and is still reachable, it is simply no longer in anybody's way.
    Pre-computed by :mod:`toto.quota.rates` rather than reversed here, so no
    quota template ever names ``tariffs:``.
    """
    return rates.advanced_url(code) if code else ""


# ---------------------------------------------------------------------------
# The row — one metered thing, everything about it
# ---------------------------------------------------------------------------

def _row(metric, user, *, prices=None, spend=None, levy_codes=frozenset()):
    """One metered thing: what it is, its cap, its price, and your use of it.

    ``prices`` and ``spend`` are the whole rate card and the whole spend summary,
    passed in so a page renders them with one query each rather than one per row.
    Both are ``{}`` on a host with no economy, and every price key then comes
    back None — which is the same thing a free metric produces, deliberately.
    """
    policy_model = policy_model_for(metric.app_label)
    policy = get_policy(policy_model, metric.code, user) if policy_model else None

    consumed = left = None
    if policy_model is not None and user is not None:
        period = policy.period if policy else metric.period
        consumed = used(policy_model, metric.code, user, period=period)
        left = remaining(policy_model, metric.code, user)

    limit = policy.limit if policy else None
    pct = None
    if limit:
        pct = min(float(consumed / limit * 100), 999) if consumed is not None else None

    price = (prices or {}).get(metric.code)
    is_levy = metric.code in levy_codes
    levy = levies.of(metric.code) if is_levy else None
    return {
        "metric": metric,
        "policy": policy,
        "limit": limit,
        "mode": policy.mode if policy else None,
        "period": policy.period if policy else metric.period,
        "used": consumed,
        "remaining": left,
        "pct_used": pct,
        # Clamp for the bar but keep "over" as its own fact, so the bar never
        # overflows its track while still being able to turn red.
        "pct_bar": min(int(pct), 100) if pct is not None else 0,
        "over": bool(pct is not None and pct > 100),
        "has_table": policy_model is not None,
        "price": price,
        "spent": (spend or {}).get(metric.code),
        "advanced_url": price["advanced_url"] if price else "",
        # How this thing is charged, which is the fact the old surface never
        # stated anywhere: a levy bills what you HOLD, nightly; everything else
        # bills the ACTION, before it runs. Either way from the first unit —
        # there is no free band anywhere, and a metric with no price is free
        # for everyone.
        "is_levy": is_levy,
        "levy": levy,
        "levy_unit": (levy["unit_label"] if levy else "") or metric.unit,
        # Armed and earning nothing: measured nightly, recorded, billed zero.
        "unpriced_levy": bool(is_levy and levy and levy["has_rule"]
                              and levy["active"] is not False and price is None),
    }


def _groups(user, *, prices=None, spend=None):
    """Every metered thing, grouped by the app that owns it."""
    levy_codes = levies.levy_codes()
    groups = []
    for app_label, metrics in registry.by_app().items():
        config = _app_config(app_label)
        groups.append({
            "app_label": app_label,
            "verbose_name": getattr(config, "verbose_name", app_label) if config else app_label,
            "installed": config is not None,
            "rows": [_row(m, user, prices=prices, spend=spend, levy_codes=levy_codes)
                     for m in metrics],
        })
    return groups


# ---------------------------------------------------------------------------
# L1 — the collection view
# ---------------------------------------------------------------------------

@login_required
def index(request):
    """Every metered thing, with the answers in the list.

    One table, one row per thing. The cap, the price and what is left are
    columns rather than destinations, so the common question needs no click at
    all.

    **The table is read-only.** It used to be a grid of inputs saved in one
    POST, which meant every row carried a limit box, a mode select, a price box
    and a currency select — four controls × seventeen things, all live, on the
    page people came to in order to LOOK something up. Editing now happens one
    thing at a time in a modal, so the reading surface stays a reading surface
    and a mis-click cannot silently reprice the platform.
    """
    is_staff = request.user.is_staff

    if request.method == "POST":
        _staff_only(request)
        if _set_charging_currency(request):
            return redirect("quota:index")
        return redirect("quota:index")

    prices = rates.rate_card()
    spend = rates.spend_by_metric(request.user)
    groups = _groups(request.user, prices=prices, spend=spend)
    rows = [r for g in groups for r in g["rows"]]

    pricing = rates.pricing_enabled()
    at_limit = sum(1 for r in rows if r["over"] or (r["limit"] and r["remaining"] == 0))
    return _render(request, "quota/index.html", {
        "groups": groups,
        "metric_count": len(registry),
        "is_staff_view": is_staff,
        # Money columns are ABSENT on an unbilled host, not blank: the question
        # does not exist there, so the page must not ask it.
        "pricing_enabled": pricing,
        "levy_enabled": levies.levy_enabled(),
        "price_asset": rates.price_asset_symbol(),
        "billing_assets": rates.billing_assets() if pricing else [],
        "modes": Mode.editable_choices(),
        "balance": rates.balance_of(request.user),
        "wallet_url": rates.wallet_url(),
        "kpi_actions": sum((r["used"] or 0) for r in rows),
        "kpi_at_limit": at_limit,
        "kpi_spent": _total_spend(spend),
        "unpriced_levies": levies.unpriced_levies() if is_staff else [],
        "taxes_url": reverse("quota:taxes"),
    })


def _set_charging_currency(request) -> bool:
    """Re-denominate the whole rate card in one asset. True when something moved.

    ONE charging currency, chosen once, applied everywhere — because the
    alternative was a currency select on every row of a seventeen-row grid, and
    a rate card that mixes currencies is a rate card nobody can add up. There is
    no exchange rate anywhere in this ledger (``get_exchange_rate`` refuses every
    cross-asset pair), so a mixed card cannot even be totalled, which is why
    ``ingress_tariffs`` already asserted everything must be priced in one asset.
    This makes that assertion something an operator can satisfy with one click
    rather than something that fails their seed.
    """
    asset_id = (request.POST.get("charging_currency") or "").strip()
    if not asset_id:
        return False
    try:
        moved = rates.set_charging_currency(asset_id)
    except ValueError as exc:
        messages.error(request, str(exc))
        return False
    if moved:
        messages.success(
            request,
            _("Everything is now charged in %(symbol)s. Prices keep their "
              "numbers — this changes the denomination, not what anything "
              "costs, because the ledger has no exchange rate to convert with.")
            % {"symbol": moved})
    return True


#: "the caller said nothing", which is not the same as "the caller said blank".
#: Blank means erase the limit; absent means leave it alone.
_UNSET = object()


def _set_default_limit(policy_model, metric, limit=_UNSET, mode=None):
    """Set or drop the everyone-policy for one metric.

    A blank limit deletes the row rather than storing zero — nothing is limited
    until a policy exists, so an absent policy is how "unlimited" is spelled.
    A mode arriving without a limit still applies to an existing row, which is
    what makes the mode select work on rows that are unlimited.
    """
    existing = policy_model.objects.filter(metric_code=metric.code, user__isnull=True).first()

    if limit is None:
        if existing is not None:
            existing.delete()
        return
    if limit is _UNSET and existing is None:
        # Mode alone, on a metric with no policy: nothing to attach it to, and
        # inventing an unlimited-but-tracked row would be a limit nobody set.
        return
    if existing is None:
        existing = policy_model(metric_code=metric.code, user=None,
                                name=metric.label, unit=metric.unit,
                                period=metric.period)
    if limit is not _UNSET:
        existing.limit = limit
    if mode:
        existing.mode = mode
    existing.active = True
    existing.save()


# ---------------------------------------------------------------------------
# L2 — one metered thing
# ---------------------------------------------------------------------------

@login_required
def metric_detail(request, code):
    """Everything about ONE metered thing, in disclosure order.

    Always visible: what it is, your usage, its limit, its price, and — when it
    is a levy — whether it is armed. One click deeper: how it is charged, its
    time dials, its advanced pricing, its recent
    activity.

    Not staff-only. A member has every reason to be here ("what does this cost
    me, how much is left"), and sees the numbers without the editors. Each POST
    action re-checks ``is_staff`` on its own.
    """
    metric = registry.get(code)
    if metric is None:
        raise Http404(f"No metric registered as {code!r}.")

    is_staff = request.user.is_staff
    policy_model = policy_model_for(metric.app_label)

    if request.method == "POST":
        _staff_only(request)
        response = _detail_post(request, metric, policy_model)
        if response is not None:
            return response

    prices = rates.rate_card()
    row = _row(metric, request.user, prices=prices,
               spend=rates.spend_by_metric(request.user),
               levy_codes=levies.levy_codes())

    default = form = None
    if policy_model is not None:
        default = policy_model.objects.filter(metric_code=code).first()
        if is_staff:
            form = policy_form_for(policy_model)(
                instance=default,
                initial=None if default else {
                    "unit": metric.unit, "period": metric.period, "active": True},
            )

    pricing = rates.pricing_enabled()
    return _render(request, "quota/metric_detail.html", {
        "metric": metric,
        "row": row,
        "is_staff_view": is_staff,
        "has_table": policy_model is not None,
        # Templates cannot read _meta, so hand over the one bit they show.
        "policy_table": policy_model._meta.db_table if policy_model else "",
        "default": default,
        "form": form,
        "pricing_enabled": pricing,
        "levy_enabled": levies.levy_enabled(),
        "price_asset": rates.price_asset_symbol(),
        "billing_assets": rates.billing_assets() if pricing else [],
        "modes": Mode.editable_choices(),
        "advanced_url": _advanced_url(code),
        "my_levy": levies.my_levy(code, request.user) if row["is_levy"] else None,
        # The dial roll-up belongs to exactly one metered thing: the one that
        # bills held time. Everywhere else this is None and the section is gone.
        "dials": times.dials_for_user(request.user) if code == "time.hold" else None,
        "dial_set_url": times.set_url(),
        "recent": _recent_events(metric, policy_model, request.user, is_staff),
        "balance": rates.balance_of(request.user),
        "wallet_url": rates.wallet_url(),
        "taxes_url": reverse("quota:taxes"),
    })


def _detail_post(request, metric, policy_model):
    """Handle one edit on the thing's page. Returns a redirect, or None."""
    action = request.POST.get("action")
    code = metric.code

    if action == "default" and policy_model is not None:
        default = policy_model.objects.filter(metric_code=code).first()
        # Blank means unlimited, exactly as in the grid. The ModelForm makes
        # `limit` required, so the detail page could not express "unlimited" at
        # all while the grid could — the same field, two opposite vocabularies.
        raw = (request.POST.get("limit") or "").strip()
        if raw == "":
            _set_default_limit(policy_model, metric, None)
            messages.success(request, _("No limit — this is now unlimited."))
            return redirect("quota:metric_detail", code=code)
        form = policy_form_for(policy_model)(request.POST, instance=default)
        if form.is_valid():
            policy = form.save(commit=False)
            policy.metric_code = code
            if not policy.name:
                policy.name = metric.label
            policy.save()
            messages.success(request, _("Limit saved."))
            return redirect("quota:metric_detail", code=code)
        messages.error(request, _("That limit could not be saved."))
        return None

    if action == "price":
        raw = request.POST.get("price")
        try:
            if (raw or "").strip() == "":
                rates.clear_price(code)
                messages.success(request, _("No price — this is now free."))
            else:
                # No asset argument: the platform charges in one currency,
                # chosen once at the rate desk. A per-price currency was a
                # select on every row of a rate card that cannot be added up
                # across currencies anyway — there is no exchange rate here.
                rates.set_price(code, raw)
                messages.success(request, _("Price saved."))
        except ValueError as exc:
            messages.error(request, str(exc))
            return None
        return redirect("quota:metric_detail", code=code)

    if action == "levy":
        # Arming AND price, in one form, in one save — because for a levy they
        # are one decision. Splitting them is what let a rule be armed and
        # unpriced: measured every night, recorded, billed nothing. The two
        # knobs lived on two screens in two apps and neither was sufficient
        # alone, and the only warning was a deploy-time log line.
        #
        # The price is written FIRST so `set_armed` can see it: its guard reads
        # the rate card, and arming without one is refused.
        try:
            raw_price = request.POST.get("price")
            if (raw_price or "").strip() == "":
                rates.clear_price(code)
            else:
                rates.set_price(code, raw_price)
            levies.set_armed(code, bool(request.POST.get("levy_active")))
        except levies.UnpricedLevy as exc:
            messages.error(request, str(exc))
            return None
        except ValueError as exc:
            messages.error(request, str(exc))
            return None
        messages.success(request, _("Levy saved."))
        return redirect("quota:metric_detail", code=code)

    return None


def _recent_events(metric, policy_model, user, is_staff, limit=10):
    """The last few usage events for this thing — theirs, or everyone's."""
    if policy_model is None:
        return []
    events = getattr(policy_model, "events", None)
    if events is None:
        return []
    qs = events.objects.filter(metric_code=metric.code)
    if not is_staff:
        qs = qs.filter(user=user)
    return list(qs.select_related("user").order_by("-occurred_at")[:limit])


# ---------------------------------------------------------------------------
# L3 — how you are charged
# ---------------------------------------------------------------------------

@login_required
def taxes(request):
    """Every KIND of charge this platform makes, read-only, with a way to each.

    Information only, deliberately. The knobs live on the things they belong to
    — a levy's arming on the levied thing, a price on the priced thing — and
    duplicating them here would recreate the two-places-to-set-one-number
    problem this restructure exists to end. What this page adds is the thing no
    screen ever said: that there are several distinct ways to be charged, that
    they run at different times, and which is which.
    """
    return _render(request, "quota/taxes.html", {
        "kinds": _charge_kinds(request.user),
        "is_staff_view": request.user.is_staff,
        "pricing_enabled": rates.pricing_enabled(),
    })


def _charge_kinds(user):
    """The charging kinds that exist ON THIS HOST, as plain data.

    Built from the registries rather than written down, so a kind cannot appear
    on a host that cannot perform it: no levy providers, no levy row; no
    ``toto.tax``, no holding fee.
    """
    priced = rates.rate_card()
    kinds = [{
        "key": "action",
        "name": _("Metered action"),
        "charges": _("A price for each action, taken before the work runs."),
        "when": _("Per action"),
        "free": _("Nothing — charged from the first unit."),
        "things": sorted(code for code in priced if code not in levies.levy_codes()),
        "edit_label": _("Set prices on each thing"),
        "edit_url": reverse("quota:index"),
    }]

    for code in sorted(levies.levy_codes()):
        metric = registry.get(code)
        levy = levies.of(code)
        if metric is None or levy is None:
            continue
        kinds.append({
            "key": f"levy:{code}",
            "name": _("Levy — %(label)s") % {"label": metric.label},
            "charges": _("Everything you hold, every night."),
            "when": _("Nightly"),
            "free": (_("Nothing — it is billed from the first %(unit)s.")
                     % {"unit": levy["unit_label"] or metric.unit}
                     if levy["has_rule"] else _("No rule — nothing is levied yet.")),
            "things": [code],
            "consequence": levy["consequence"],
            "edit_label": _("Open %(label)s") % {"label": metric.label},
            "edit_url": reverse("quota:metric_detail", args=[code]),
        })

    kinds.extend(_economy_charge_kinds())
    return kinds


def _economy_charge_kinds():
    """Charges that are not keyed by a metric.

    Empty today, and kept as a seam rather than deleted: the tribute a company
    paid the platform lived here, and the subscription that replaced it is keyed
    by a metric, so it arrives through the loop above like everything else.

    is_installed BEFORE each import: a host can pin the economy wheel without
    installing these apps, and importing their models raises RuntimeError out of
    Django's model metaclass, which ``except ImportError`` never catches.
    """
    return []


def _safe_url(name: str, *args) -> str:
    try:
        return reverse(name, args=[a for a in args if a is not None])
    except Exception:  # noqa: BLE001 - unmounted namespace is a soft edge
        return ""


# ---------------------------------------------------------------------------
# L4 — where the platform's money comes from
# ---------------------------------------------------------------------------

@login_required
def fees(request):
    """Income by source — the operator's view of money in.

    Staff only now. This URL used to render two unrelated pages depending on who
    asked: income and editors for staff, and "what am I charged" for everyone
    else. The second of those belongs on the metered things themselves, where
    the prices and the usage are, and it lives there now — so what is left is
    one page about one object, income sources, which is not a metered thing.
    """
    _staff_only(request)
    from . import feeboard

    board = feeboard.income_board()
    return _render(request, "quota/fees.html", {
        "board": board,
        "income_pie_json": feeboard.income_pie_json(board),
        "billing_enabled": apps.is_installed("toto.tariffs"),
        "price_asset": rates.price_asset_symbol(),
        "taxes_url": reverse("quota:taxes"),
    })


@login_required
def my_usage(request, app_label=None):
    """One app's metered things, scoped to the signed-in user.

    Kept because every metered app's own "Usage" button lands here, and narrowing
    to one app is a real need the collection view answers less directly. It is
    the same rows as :func:`index`, filtered — not a second vocabulary.
    """
    if app_label is not None and not registry.for_app(app_label):
        # A tab pointing at an app that meters nothing would be a dead link;
        # 404 rather than render a convincingly empty page.
        raise Http404(f"{app_label!r} meters nothing on this host.")

    metrics = registry.for_app(app_label) if app_label else registry.all()
    prices = rates.rate_card()
    spend = rates.spend_by_metric(request.user)
    levy_codes = levies.levy_codes()
    rows = [r for r in (_row(m, request.user, prices=prices, spend=spend,
                             levy_codes=levy_codes)
                        for m in metrics) if r["has_table"]]

    at_limit = sum(1 for r in rows if r["over"] or (r["limit"] and r["remaining"] == 0))
    return _render(request, "quota/my_usage.html", {
        "rows": rows,
        "scope_app": app_label,
        "pricing_enabled": rates.pricing_enabled(),
        "price_asset": rates.price_asset_symbol(),
        "balance": rates.balance_of(request.user),
        "wallet_url": rates.wallet_url(),
        "kpi_actions": sum((r["used"] or 0) for r in rows),
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
