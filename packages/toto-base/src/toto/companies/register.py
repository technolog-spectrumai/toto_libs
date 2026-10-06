"""The register's arithmetic (stage 65): rows, the total, percentages.

A percentage is of the RECORDED holdings and nothing else: one holding's
quantity over the sum of the quantities recorded for that company, two
decimal places, rounded half up. It is not a share of the company's capital:
shares nobody recorded here are not in the sum. With nothing recorded (no
row, or only zeros) there is no percentage at all, ``None``, which the pages
draw as a dash: nothing is ever divided by zero.

Rounded parts need not add to exactly 100.
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
