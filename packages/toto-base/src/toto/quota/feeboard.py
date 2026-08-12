"""The arithmetic behind the Fees page.

Kept out of ``views.py`` so it can be tested without a request, and because the
whole file is one careful idea: **the registry says what a source means, the
ledger says how much it earned.** Nothing here asks a source for a number.

Two constraints shape every function below.

*No summing across assets.* ``get_exchange_rate()`` refuses every cross-asset
pair and the doctrine says so outright — "No FX, anywhere"
(portal/hierarchical_economy.md). A total that added ASR to BANANA would be a
number with no meaning, so income is reported in ONE asset: the contractual one,
whatever ``gas_asset()`` resolves to. Anything earned in another asset is not
hidden — it is reported separately as drift, below.

*One billing currency is only half enforced.* The database guarantees one active
local ``CurrencyContract``, so there is exactly one contractual asset. It does
NOT constrain ``TariffItem.charged_asset``, which the staff screens will
happily set to anything active. So the page states
what the platform is contracted for and lists everything priced against
something else, with a link to fix each one.

Everything degrades rather than raising: a host with no ledger, no economy wheel
or an empty registry gets an empty board and a page that says so.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal

from django.urls import NoReverseMatch, reverse

from .fees import registry

#: The repo's de-facto categorical palette, as used by portfolio/queries.py.
PALETTE = ["#4F46E5", "#10B981", "#F59E0B", "#EF4444", "#3B82F6", "#8B5CF6"]


@dataclass
class IncomeRow:
    """One source's earnings, in the contractual asset."""

    code: str
    label: str
    description: str
    icon: str
    account_code: str
    settings_url: str
    base_units: int = 0
    amount: Decimal = Decimal("0")
    percent: float = 0.0

    @property
    def has_income(self) -> bool:
        return self.base_units > 0


@dataclass
class DriftRow:
    """Something priced in an asset this platform is not contracted for."""

    kind: str            # "price" | "community fee"
    what: str            # the metric code, or the asset the policy taxes
    asset: str           # the off-contract ticker
    fix_url: str = ""


@dataclass
class FeeBoard:
    """Everything the Fees page needs, as plain data."""

    asset_unit: str = ""
    rows: list = field(default_factory=list)
    drift: list = field(default_factory=list)
    total_base_units: int = 0
    total: Decimal = Decimal("0")
    billing: bool = False          # does this platform bill at all?

    # The other direction. This board was income-only for its whole life, on a
    # platform where nothing ever debited a fee account — so it showed a number
    # that could only grow and never said where any of it went. It goes to the
    # payroll, and now the page says so.
    paid_out_base_units: int = 0
    paid_out: Decimal = Decimal("0")
    held_base_units: int = 0
    held: Decimal = Decimal("0")

    @property
    def earning_rows(self) -> list:
        return [r for r in self.rows if r.has_income]


def _url_or_blank(url_name: str) -> str:
    """A url name resolved, or "" — an unmounted app gets a card, not a 500."""
    if not url_name:
        return ""
    try:
        return reverse(url_name)
    except NoReverseMatch:
        return ""


def _contractual_asset():
    """The asset this platform bills in, or None when it does not bill."""
    try:
        from toto.tariffs.rate_card import gas_asset
    except ImportError:
        return None
    try:
        return gas_asset()
    except Exception:  # noqa: BLE001 - an unseeded or unbilled host has none
        return None


def income_board(*, since=None, until=None) -> FeeBoard:
    """What each registered source earned, in the contractual asset.

    Amounts come from ``LedgerEntry`` credits into each source's account. That
    is the only common denominator: the four sources store their journals three
    different ways and one of them (the exchange commission) has no journal model
    at all — it exists purely as ledger entries.
    """
    from django.db.models import Sum

    board = FeeBoard()
    asset = _contractual_asset()
    if asset is None or not len(registry):
        return board

    board.billing = True
    board.asset_unit = asset.unit_name

    from toto.assets.models import LedgerAccount, LedgerEntry, from_base_units

    codes = registry.account_codes()
    accounts = dict(
        LedgerAccount.objects.filter(code__in=codes).values_list("code", "pk"))

    entries = LedgerEntry.objects.filter(
        asset=asset,
        account_id__in=accounts.values(),
        # Credits only. A debit out of a revenue account is a refund or a
        # sweep, and netting it off here would be right — but the sign is what
        # makes it right, so it is summed rather than filtered.
        transaction__posted=True,
    )
    if since is not None:
        entries = entries.filter(created_at__gte=since)
    if until is not None:
        entries = entries.filter(created_at__lt=until)

    totals = dict(
        entries.values_list("account_id").annotate(
            total=Sum("amount_base_units")).values_list("account_id", "total"))

    for source in registry:
        account_pk = accounts.get(source.account_code)
        raw = int(totals.get(account_pk) or 0) if account_pk else 0
        raw = max(0, raw)   # a net-negative source is 0%, never a negative slice
        board.rows.append(IncomeRow(
            code=source.code,
            label=str(source.label),
            description=str(source.description),
            icon=source.icon,
            account_code=source.account_code,
            settings_url=_url_or_blank(source.settings_url),
            base_units=raw,
            amount=from_base_units(raw, asset.decimals),
        ))

    board.total_base_units = sum(r.base_units for r in board.rows)
    board.total = from_base_units(board.total_base_units, asset.decimals)
    if board.total_base_units:
        for row in board.rows:
            row.percent = round(100 * row.base_units / board.total_base_units, 1)

    board.rows.sort(key=lambda r: (-r.base_units, r.code))
    _add_outgoings(board, asset, since=since, until=until)
    board.drift = off_contract_rows(asset)
    return board


def _add_outgoings(board, asset, *, since=None, until=None) -> None:
    """What the treasury paid out, and what it is still holding.

    Read from the ledger rather than from ``StipendPayment``, for the same
    reason income is: the ledger is the thing that actually moved, and a row
    that says PAID while no entry exists would be the one lie this page must
    never tell.
    """
    from django.db.models import Sum

    from toto.assets.models import LedgerAccount, LedgerEntry, from_base_units
    from toto.assets.queries import get_asset_balance

    try:
        from toto.tariffs.rate_card import REVENUE_ACCOUNT_CODE
    except ImportError:  # pragma: no cover - no tariffs, no treasury
        return

    treasury = LedgerAccount.objects.filter(code=REVENUE_ACCOUNT_CODE).first()
    if treasury is None:
        return

    out = LedgerEntry.objects.filter(
        asset=asset, account=treasury, transaction__posted=True,
        amount_base_units__lt=0)
    if since is not None:
        out = out.filter(created_at__gte=since)
    if until is not None:
        out = out.filter(created_at__lt=until)

    total = int(out.aggregate(total=Sum("amount_base_units"))["total"] or 0)
    board.paid_out_base_units = abs(total)
    board.paid_out = from_base_units(abs(total), asset.decimals)

    held = get_asset_balance(asset, treasury)
    board.held_base_units = held
    board.held = from_base_units(held, asset.decimals)


def off_contract_rows(asset) -> list:
    """Prices and fee policies denominated in something else.

    Not an error — the data model permits it and a demo tariff in the seeder
    uses it deliberately. But it is income the board above cannot count and the
    doctrine says should not exist, so it is named rather than dropped.
    """
    rows: list = []
    if asset is None:
        return rows

    try:
        from toto.tariffs.models import TariffItem
    except ImportError:
        return rows

    items = (TariffItem.objects.filter(active=True)
             .exclude(charged_asset=asset)
             .select_related("metric", "charged_asset"))
    for item in items:
        rows.append(DriftRow(
            kind="price",
            what=item.metric.code if item.metric_id else item.name,
            asset=item.charged_asset.unit_name,
            fix_url=_url_or_blank("quota:index"),
        ))

    # Income retargeted away from the account its source counts. The most
    # fragile configurable thing in the area and the least visible: a
    # TariffItem's receiving_account is editable on an "advanced" page most
    # operators never open, FeeSource.account_code is a frozen constant, and
    # nothing validated that the two still agree — so the money kept arriving
    # and the card silently stopped counting it.
    rows.extend(_misrouted_rows())

    return rows


def _misrouted_rows() -> list:
    """Priced metrics crediting an account no registered source counts."""
    out: list = []
    try:
        from toto.tariffs.models import TariffItem
    except ImportError:
        return out

    known = {source.account_code for source in registry.all() if source.account_code}
    if not known:
        return out
    items = (TariffItem.objects.filter(active=True)
             .exclude(receiving_account__code__in=known)
             .select_related("metric", "receiving_account", "charged_asset"))
    for item in items:
        if item.receiving_account_id is None:
            continue
        out.append(DriftRow(
            kind="misrouted income",
            what=(item.metric.code if item.metric_id else item.name),
            asset=item.receiving_account.code,
            fix_url=_url_or_blank("quota:index"),
        ))
    return out


def income_pie_json(board: FeeBoard) -> str:
    """The Chart.js payload, per ``oya/partials/chart.html``'s contract.

    A JSON *string* of ``{chart_type, labels, datasets}``, the shape
    portfolio/queries.py::cap_chart_json established. Returns "" when there is
    nothing to draw, so the template can omit the canvas rather than render an
    empty one.

    One asset per pie, always — see this module's header.
    """
    rows = board.earning_rows
    if not rows:
        return ""
    return json.dumps({
        "chart_type": "pie",
        "labels": [r.label for r in rows],
        "datasets": [{
            "label": board.asset_unit,
            "data": [r.base_units for r in rows],
            "backgroundColor": [PALETTE[i % len(PALETTE)]
                                for i in range(len(rows))],
            "borderWidth": 0,
        }],
    })
