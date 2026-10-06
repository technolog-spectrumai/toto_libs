"""The register's arithmetic (stage 65): rows, the total, percentages.

A percentage is of the RECORDED holdings and nothing else: one holding's
quantity over the sum of the quantities recorded for that company, two
decimal places, rounded half up. It is not a share of the company's capital:
shares nobody recorded here are not in the sum. With nothing recorded (no
row, or only zeros) there is no percentage at all, ``None``, which the pages
draw as a dash: nothing is ever divided by zero.

Rounded parts need not add to exactly 100.

THE OWNERSHIP RING (2026-10-06, the owner: "add pie chart to show the
structure of ownsership - use similat trick that file vault how mauch each
file types takes space"). ``chart_of`` is what the Shareholdings tab hands
its doughnut: one slice per holding with shares, the largest first, and
where there are more than ``CHART_SLICES`` of them the smallest together as
one last slice. It is DATA (``json_script``): a holder's name is a label
drawn on a canvas, never markup. Nothing to draw where there is nothing to
take a share of.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

HUNDREDTH = Decimal("0.01")

#: A whole, non-negative number of shares as typed: ASCII digits only, at
#: most 18 of them (the column is a signed 64-bit integer).
WHOLE = re.compile(r"[0-9]{1,18}")


def percentage(quantity, total) -> Decimal | None:
    """``quantity`` as a percentage of ``total``, or None where ``total`` is
    nothing."""
    if not total or total <= 0:
        return None
    return (Decimal(quantity) * 100 / Decimal(total)).quantize(HUNDREDTH, ROUND_HALF_UP)


#: At most this many slices in the ownership ring; with more holders the
#: last slice is every smaller holding together.
CHART_SLICES = 10

#: The slices' colours, in order: the hues of the vault's "files by type"
#: ring first. The grey is the vault's "anything else", here the last slice
#: of a long register.
CHART_COLOURS = ("#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#8b5cf6",
                 "#ec4899", "#0ea5e9", "#84cc16", "#f97316", "#14b8a6")
CHART_REST_COLOUR = "#94a3b8"


def parse_quantity(text) -> int | None:
    """The whole, non-negative number ``text`` writes, or None: no sign, no
    fraction, no exponent, no separator, nothing but digits."""
    text = str(text if text is not None else "").strip()
    return int(text) if WHOLE.fullmatch(text) else None


@dataclass
class Row:
    """One holding as a page draws it."""

    holding: object
    quantity: int
    percentage: Decimal | None


@dataclass
class Register:
    rows: list = field(default_factory=list)
    total: int = 0

    @property
    def holders(self) -> int:
        return len(self.rows)

    @property
    def empty(self) -> bool:
        """Nothing to take a percentage of: no rows, or only zeros."""
        return self.total <= 0


def register_of(community) -> Register:
    """The company's register: every holding, the largest first, each with
    its percentage of the total recorded."""
    from .models import ShareHolding

    holdings = list(ShareHolding.objects.filter(community=community)
                    .select_related("person").order_by("-quantity", "person__display_name", "pk"))
    total = sum(holding.quantity for holding in holdings)
    return Register(rows=[Row(holding, holding.quantity, percentage(holding.quantity, total))
                          for holding in holdings],
                    total=total)


def holdings_of(person, viewer) -> list[Row]:
    """``person``'s holdings as ``viewer`` may see them: only in communities
    that are companies and whose register the viewer may see
    (``access.visible_companies``), each with its percentage of that
    company's recorded total. By company name."""
    from django.db.models import Sum

    from .access import visible_companies
    from .models import ShareHolding

    if person is None or not getattr(person, "pk", None):
        return []
    holdings = list(ShareHolding.objects.filter(person=person,
                                                community__in=visible_companies(viewer))
                    .select_related("community").order_by("community__name", "pk"))
    if not holdings:
        return []
    totals = dict(ShareHolding.objects
                  .filter(community_id__in=[holding.community_id for holding in holdings])
                  .values_list("community_id").annotate(total=Sum("quantity")))
    return [Row(holding, holding.quantity,
                percentage(holding.quantity, totals.get(holding.community_id) or 0))
            for holding in holdings]


def chart_of(register: Register, rest_label: str) -> dict | None:
    """The ownership ring of ``register``, or None where it is empty.

    ``labels`` the holders' names, ``values`` their quantities (what the
    ring is drawn from), ``shares`` the same quantities as text (what a
    slice's tip states: a number of 18 digits is not exact in a browser),
    ``colours`` one per slice. A holding of zero has no slice. With more
    than ``CHART_SLICES`` holders the last slice, ``rest_label``, is the
    smaller holdings together, so the slices always add to the total."""
    held = [row for row in register.rows if row.quantity > 0]
    if register.empty or not held:
        return None
    shown, rest = held, []
    if len(held) > CHART_SLICES:
        shown, rest = held[:CHART_SLICES - 1], held[CHART_SLICES - 1:]
    labels = [row.holding.person.display_name for row in shown]
    values = [row.quantity for row in shown]
    colours = [CHART_COLOURS[at % len(CHART_COLOURS)] for at in range(len(shown))]
    if rest:
        labels.append(str(rest_label))
        values.append(sum(row.quantity for row in rest))
        colours.append(CHART_REST_COLOUR)
    return {"labels": labels, "values": values,
            "shares": [str(value) for value in values], "colours": colours}
