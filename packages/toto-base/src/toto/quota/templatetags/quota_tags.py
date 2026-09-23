"""A link from a metered app to its own slice of the usage page.

Deliberately a template tag rather than a plugin registry. `toto.quota` is
mounted unconditionally on every host, so ``quota:my_usage_app`` always
reverses — there is no NoReverseMatch to dodge and therefore nothing a registry
would buy, while `BasePlugin.register` raising on a duplicate key is a real cost
to take on for a static link.

Usage, one line in the app's own nav::

    {% load quota_tags %}
    {% quota_tab "texlab" %}
"""

from django import template
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _

from toto.quota.metrics import registry

register = template.Library()


@register.inclusion_tag("quota/partials/_quota_tab.html")
def quota_tab(app_label, label=None):
    """A usage link for this app, or nothing when it meters nothing.

    Checking the registry is what keeps the tab honest: an app that declares no
    metric would otherwise link to a page listing nothing, and a plugin left
    behind after its metric was deleted would become a dead tab. Here the answer
    follows the registry automatically.
    """
    if not registry.for_app(app_label):
        return {"url": ""}
    try:
        url = reverse("quota:my_usage_app", args=[app_label])
    except NoReverseMatch:  # pragma: no cover - quota is mounted everywhere
        url = ""
    return {"url": url, "label": label or _("Usage"),
            "icon": "fa-solid fa-gauge-high"}


@register.inclusion_tag("quota/partials/_price_hint.html", takes_context=True)
def price_hint(context, *metric_codes, label=None):
    """What this button is about to cost, next to the button.

    Everything on this platform pays, and until now it only said so afterwards —
    on the usage page, or in a 402 when the money had already run out. A price is
    a thing to know BEFORE clicking, so this puts it on the control that spends
    it::

        {% load quota_tags %}
        <button>Export PDF {% price_hint "memo.pdf" %}</button>

    Several codes when one action meters several things — a vault upload is
    charged per call AND per megabyte, and quoting only the first would be a
    quote that undercounts::

        {% price_hint "storage.request" "storage.transfer_mb" %}

    **Renders nothing at all** when there is no price to name: a host with no
    economy (studio and aurelian pin no tariffs wheel), a metric nobody has
    priced, or a price of zero. That is the common case on two of three hosts, so
    the empty answer has to be silent rather than an apologetic "free" badge on
    every button in the product.

    Prices come from ``rates.price_of``, which already returns plain data and {}
    on an unbilled host — so this never imports tariffs and is safe in a template
    on any host in the fleet.
    """
    from toto.quota import rates

    # One rate-card read per request, not one per tag. A toolbar carries several
    # of these and the card is a table scan with three joins — rendering the
    # cyprian menu was three identical queries before this cache, and the number
    # grows with every button anyone annotates. Stashed on the request so it also
    # spans {% include %}, which a render_context cache would not.
    request = context.get("request")
    card = getattr(request, "_toto_rate_card", None) if request is not None else None
    if card is None:
        card = rates.rate_card()
        if request is not None:
            request._toto_rate_card = card

    quotes = []
    for code in metric_codes:
        row = card.get(code)
        if not row:
            continue
        price = row.get("price_display")
        if not price:          # unpriced, or priced at zero — say nothing
            continue

        # The rate card's own unit wins over the metric's: an admin can price
        # "per 10 MB" on a metric declared in MB, and the number shown has to
        # match the number charged.
        metric = registry.get(code)
        unit = row.get("unit_code") or (metric.unit if metric else "")
        quantity = row.get("unit_quantity") or 1
        per = ""
        if unit and unit != "request":
            per = f"{quantity} {unit}" if quantity and quantity != 1 else unit

        quotes.append({
            "code": code,
            "label": metric.label if metric else code,
            "price": price,
            "asset": row.get("asset", ""),
            "per": per,
            # Which mana pool this draws on, when it is priced in one — the
            # badge a member reads is "−3 security mana", never an asset ticker.
            "role": _mana_role(request, code, row.get("asset_id")),
        })

    return {"quotes": quotes, "label": label}


def _mana_role(request, code, asset_id):
    """The pool a priced metric draws on, or "" — never raises.

    Only when the price really is in a pool's asset: a host with the mana app
    but no pools yet still prices in gas, and must still say so. The pool
    assets are read once per request, like the rate card above.
    """
    from django.apps import apps

    if not asset_id or not apps.is_installed("toto.mana"):
        return ""
    try:
        from toto.mana import services

        pooled = getattr(request, "_toto_mana_pool_assets", None) if request is not None else None
        if pooled is None:
            pooled = {p.asset_id: p.role for p in services.pools().values()}
            if request is not None:
                request._toto_mana_pool_assets = pooled
        return pooled.get(asset_id, "")
    except Exception:  # noqa: BLE001 - a hint must never break a toolbar
        return ""
