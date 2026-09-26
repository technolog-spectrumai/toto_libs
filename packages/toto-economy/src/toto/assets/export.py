"""Taking the transaction list away with you, as HTML or as XML.

Two formats, one dataset, and the dataset is **whatever the page was showing**:
the export reads the same filters through the same function the view does, so
"export what I am looking at" cannot drift into "export everything" the next
time somebody adds a filter to the page.

## Why the amount and the parties are computed

A ``LedgerTransaction`` carries no amount and no sender: this is a double-entry
ledger, so the money is in its ``LedgerEntry`` rows — negative on the account it
left, positive on the account it reached. An export that quoted a single
"amount" column off the transaction would be quoting a field that does not
exist. Here the amount is the total credited, and the parties are the accounts
on each side, which is the same thing the detail page shows a person.

## Deterministic XML

Same rows in, same bytes out — no generation timestamp, no dict ordering, no
locale in the numbers. That is what makes the output diffable and checksummable,
which is the only reason to prefer XML over the HTML for an archive. The order
is ``(-created_at, -id)``: created_at alone is not a total order, and two rows
in the same second would otherwise swap places between runs.

## Escaping

XML goes through ``ElementTree``, which escapes text and attributes itself; the
HTML goes through a Django template, which autoescapes. Neither builds markup by
concatenation — a reference or a description is arbitrary text that arrived from
outside, and the export is the last place it would be noticed going wrong.
"""

from __future__ import annotations

from decimal import Decimal
from xml.etree import ElementTree as ET

#: How many rows one export may carry. Bounded because an unbounded export of a
#: busy ledger is a document nothing can open — and because the work happens in
#: the request, so it is also a request nothing can serve.
#:
#: Reaching it is REFUSED rather than truncated. A cut-off export of a ledger is
#: worse than no export: it looks complete, it balances against nothing, and the
#: person holding it has no way to tell. The answer is a narrower time span,
#: which the caller must always give anyway.
MAX_ROWS = 50_000


class ExportTooLarge(Exception):
    """More rows than one export may carry. Carries the count, to say so."""

    def __init__(self, count: int):
        self.count = count
        super().__init__(f"{count} transactions is more than {MAX_ROWS}")


class TimeSpanRequired(Exception):
    """An export must name the window it covers."""


def parse_day(raw: str):
    """A ``YYYY-MM-DD`` string as an aware datetime at the start of that day.

    Returns None for anything unparseable, including "". The caller decides
    whether a missing bound is a refusal — the page tolerates one, the export
    does not.
    """
    from datetime import datetime

    from django.utils import timezone as dj_timezone

    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        naive = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return None
    if dj_timezone.is_naive(naive):
        return dj_timezone.make_aware(naive, dj_timezone.get_default_timezone())
    return naive


def in_community(transactions, community):
    """Only transactions touching an account of a member of ``community``
    (2026-09-26): the person link is ``LedgerAccount.user``, membership is
    ``Person.communities``. Platform accounts (no user) never match."""
    return transactions.filter(
        entries__account__user__community_profile__communities=community).distinct()


def filtered_transactions(*, asset: str = "", tx_type: str = "",
                          since=None, until=None, community=None):
    """The queryset the transactions page shows, in a total order.

    Shared with the view rather than reimplemented, so the export and the page
    can never disagree about what "currently filtered" means.

    ``until`` is INCLUSIVE of its whole day: a person choosing 1st to 31st means
    the 31st, and ``created_at <= 31st 00:00`` would silently drop everything
    that happened on it.

    ``-created_at`` alone is the page's order and is NOT a total order — two
    transactions in the same second may come back either way round. The ``-id``
    tie-break is what makes an XML export of unchanged data byte-identical.
    """
    from datetime import timedelta

    from .models import LedgerTransaction

    transactions = (LedgerTransaction.objects
                    .select_related("asset")
                    .order_by("-created_at", "-id"))
    if asset:
        transactions = transactions.filter(asset__unit_name__iexact=asset)
    if tx_type:
        transactions = transactions.filter(transaction_type=tx_type)
    if since is not None:
        transactions = transactions.filter(created_at__gte=since)
    if until is not None:
        transactions = transactions.filter(created_at__lt=until + timedelta(days=1))
    if community is not None:
        transactions = in_community(transactions, community)
    return transactions


def transaction_rows(transactions, *, limit: int | None = None):
    """One flat record per transaction, or ExportTooLarge.

    Counted BEFORE anything is built: a refusal that arrives after fifty
    thousand rows have been rendered is a refusal that cost the same as the
    export would have.

    Entries are prefetched — a row needs its sides, and fetching them per
    transaction turns a large export into one query per row.
    """
    from django.db.models import Prefetch

    from .models import LedgerEntry

    # Read at CALL time, not bound as a default argument: a default is
    # evaluated once at import, so the ceiling could not be changed — by a test,
    # or by a host that wanted a different one — after this module loaded.
    if limit is None:
        limit = MAX_ROWS

    count = transactions.count()
    if count > limit:
        raise ExportTooLarge(count)

    queryset = transactions.prefetch_related(
        Prefetch("entries",
                 queryset=LedgerEntry.objects.select_related("account", "asset")))
    return [_row(transaction) for transaction in queryset]


def _row(transaction) -> dict:
    """One transaction, flattened — with the money read off its entries."""
    credited = 0
    senders, recipients = [], []
    decimals = transaction.asset.decimals if transaction.asset_id else 0

    for entry in transaction.entries.all():
        code = entry.account.code if entry.account_id else ""
        if entry.amount_base_units < 0:
            senders.append(code)
        elif entry.amount_base_units > 0:
            recipients.append(code)
            credited += entry.amount_base_units

    return {
        # The reference is the identifier a person recognises and the column the
        # database holds unique; the uuid is what another system would match on.
        "reference": transaction.reference or "",
        "uuid": str(transaction.uuid),
        "asset": transaction.asset.unit_name if transaction.asset_id else "",
        "amount_base_units": credited,
        "amount": _display(credited, decimals),
        "sender": ", ".join(sorted(senders)),
        "recipient": ", ".join(sorted(recipients)),
        "type": transaction.transaction_type or "",
        "type_label": transaction.get_transaction_type_display(),
        "posted": bool(transaction.posted),
        "description": transaction.description or "",
        # ISO 8601 in UTC. A locale-formatted date would make the XML depend on
        # who ran the export.
        "timestamp": transaction.created_at.isoformat() if transaction.created_at else "",
    }


def _display(base_units: int, decimals: int) -> str:
    """Base units as a plain decimal string, never a float.

    ``Decimal`` and a fixed number of places: a float would round a ledger
    amount, and ``str(Decimal)`` can go exponential, which is not a number a
    spreadsheet will read back.
    """
    if not decimals:
        return str(base_units)
    scaled = Decimal(base_units).scaleb(-decimals)
    return f"{scaled:.{decimals}f}"


#: The columns, in the order both formats present them. One tuple so the two
#: cannot fall out of step.
COLUMNS = (
    ("reference", "Reference"),
    ("uuid", "UUID"),
    ("timestamp", "Timestamp"),
    ("type_label", "Type"),
    ("asset", "Asset"),
    ("amount", "Amount"),
    ("sender", "From"),
    ("recipient", "To"),
    ("description", "Description"),
)


def render_xml(rows, *, asset: str = "", tx_type: str = "",
               since: str = "", until: str = "") -> bytes:
    """Deterministic XML. Same rows in, same bytes out.

    No generated-at stamp, deliberately: it would change every run and make two
    exports of identical data compare unequal, which is the one property this
    format is chosen for.
    """
    root = ET.Element("ledger-transactions")
    # Attribute order is insertion order in ElementTree, so it is stable.
    root.set("count", str(len(rows)))
    # The window is on the ROOT as well as in the filters, because it is not a
    # filter here — it is what the document claims to be complete for. A reader
    # holding this file needs to know the period without parsing further.
    root.set("from", since or "")
    root.set("to", until or "")

    applied = ET.SubElement(root, "filters")
    applied.set("asset", asset or "")
    applied.set("type", tx_type or "")
    applied.set("from", since or "")
    applied.set("to", until or "")

    items = ET.SubElement(root, "transactions")
    for row in rows:
        element = ET.SubElement(items, "transaction")
        # Elements rather than attributes for the values: a description may be
        # long and multi-line, and attributes are the wrong shape for that.
        for key in ("reference", "uuid", "timestamp", "type", "asset",
                    "amount", "amount_base_units", "sender", "recipient",
                    "description"):
            child = ET.SubElement(element, key.replace("_", "-"))
            child.text = str(row[key])
        posted = ET.SubElement(element, "posted")
        posted.text = "true" if row["posted"] else "false"

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def render_html(rows, *, asset: str = "", tx_type: str = "",
                since: str = "", until: str = "") -> str:
    """A standalone HTML document: one file, no external anything.

    Rendered through a template so every value is autoescaped. The styling is
    inline because the point of this format is that it still opens, and still
    reads, on a machine that has never heard of this platform.
    """
    from django.template.loader import render_to_string

    return render_to_string("assets/transaction_export.html", {
        "rows": rows,
        "columns": COLUMNS,
        "asset_filter": asset,
        "type_filter": tx_type,
        "since": since,
        "until": until,
    })
