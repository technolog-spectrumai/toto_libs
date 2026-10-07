"""What a post costs, and the three moments the question is asked.

    quote(user, text_bytes=…, image_bytes=…)
        what a post of these sizes would cost this member: the estimate
        door. Stores nothing, charges nothing.
    afford_post(user, text_bytes=…, image_bytes=…)
        before anything is stored: the day's caps (429) and the funds (402,
        in the pool's own sentence). A refusal leaves no row and no image.
    settle_post(user, message, text_bytes=…, image_bytes=…)
        INSIDE the transaction that stores the message
        (``posting.post_message``): one usage event and one ledger charge
        per metric. An exception from it rolls the message back.

**The quantity is exact.** A post is charged per kilobyte of its text
(``forum.text_kb``, the UTF-8 bytes) and per kilobyte of its image
(``forum.image_kb``, the bytes before sealing). ``kilobytes(n)`` is
``Decimal(n) / 1024`` and nothing else: 1/1024 is 0.0009765625, ten decimal
places, so every whole number of bytes is a quantity the ledger's columns
hold without loss. No float is made anywhere on the way.

**The price is a row of the rate card** (``tariffs.TariffItem``), one per
metric, in storage mana: seeded by ``ingress_mana`` from
``toto/mana/colours.py`` (text 0.001 a kilobyte, an image 0.002),
overridden per host by ``settings.MANA_PRICES`` and owned by staff on the
price desk afterwards.

**The arithmetic and the rounding are the ledger's, once.** The charge is
``quantity × price``, worked in ``Decimal`` in the asset's base units (mana
has nine decimal places, so the smallest unit is 0.000000001) and rounded
ONCE, UP to a whole base unit: ``ROUND_CEILING``, the rate card row's
rounding mode ``up`` (``tariffs.services._apply_rounding``), which is what
both rows are seeded with. So one byte of text at 0.001 a kilobyte is
0.0000009765625, charged as 0.000000977; and a post never costs nothing
because it is small. The ledger then takes a member's community discount
off, as it does for every mana charge, rounding that down in the member's
favour.

**One function, asked three times.** ``lines()`` is the only place sizes
become what is billed, and ``toto.quota.charge`` hands those lines to the
ledger's one calculator (``tariffs.services.calculate_tariff_charge``)
whether the question is "what would it cost" (``quote``), "can they pay"
(``afford_post``) or "charge it" (``settle_post``). The estimate cannot
differ from the charge: it is the same code with the same input, the
member's discount and the row's settings included.

**Never twice, never for nothing.** A retry of the same press never comes
here: the posting door answers a known ``op`` with the message already
stored. Beyond that, each usage event is keyed ``<metric>:<message id>`` and
the charge is made only if the event was written, so a second settle of one
message charges nothing. The charge is in the message's transaction: a post
that fails after it is rolled back with it, and a charge the ledger refuses
(the pool ran short between the check and the charge) takes the message and
its image with it.

**Nobody is exempt.** The platform has no exemption from a mana charge, for
an administrator or anybody else, and the forum adds none: the head and an
administrator pay for what they post as every member does. Removing,
reading and voting cost nothing. On a host with no economy, or with no
price on the rate card, everything here is free and the caps still apply.

Nothing of a message is written here: a usage event and a ledger entry hold
the metric, the message's id and the number of bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, Inexact
from typing import NamedTuple

from django.db import IntegrityError, transaction
from django.utils import timezone

from toto.quota.api import check_quota
from toto.quota.charge import (InsufficientFunds, charge, check_funds_all,  # noqa: F401
                               price_for)
from toto.quota.charge import quote as ledger_quote

from . import metrics

APP = metrics.APP
TEXT_KB = metrics.TEXT_KB
IMAGE_KB = metrics.IMAGE_KB

#: A kilobyte, as a member's file manager counts one.
BYTES_PER_KB = Decimal(1024)

#: Division that refuses to round: an inexact result raises instead of
#: being quietly cut to the context's precision.
_EXACT = Context(prec=60, traps=[Inexact])

#: What the ledger entry and the usage event call the charge. Never anything
#: of the message.
LABEL = {TEXT_KB: "Forum message: text", IMAGE_KB: "Forum message: image"}

SOURCE_TYPE = "forum.ForumMessage"


def kilobytes(size: int) -> Decimal:
    """``size`` bytes as kilobytes, exactly: ``Decimal(size) / 1024``."""
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValueError("A size is a whole number of bytes, zero or more.")
    return _EXACT.divide(Decimal(size), BYTES_PER_KB)


class Line(NamedTuple):
    """One thing a post is charged for."""

    code: str
    size: int           # bytes
    quantity: Decimal   # kilobytes, exact
    unit: str


def lines(text_bytes: int, image_bytes: int) -> list[Line]:
    """What a post of these sizes is billed for: its text, then its image,
    each only if there is any. THE one place sizes become quantities; the
    estimate, the check and the charge all start here."""
    out = []
    for code, size in ((TEXT_KB, text_bytes), (IMAGE_KB, image_bytes)):
        quantity = kilobytes(size)
        if quantity:
            out.append(Line(code, size, quantity, metrics.UNIT))
    return out


def _charges(billed) -> list[tuple[str, Decimal, str]]:
    return [(line.code, line.quantity, line.unit) for line in billed]


def text_of(value: Decimal) -> str:
    """A Decimal as a plain decimal string: no exponent, no trailing zeros."""
    return format(Decimal(value).normalize(), "f")


@dataclass(frozen=True)
class Quote:
    """What a post would cost one member, in the display units of the asset
    it is charged in."""

    text: Decimal
    image: Decimal
    affordable: bool
    #: What the member holds in that asset; None where nothing is priced.
    balance: Decimal | None
    #: How the asset is named to a member ("storage mana"); "" where nothing
    #: is priced.
    label: str = ""

    @property
    def amount(self) -> Decimal:
        return self.text + self.image

    @property
    def display(self) -> str:
        """The amount as the platform writes a cost (three significant
        digits, ``toto.quota.rates.significant``) and the pool it is drawn
        from; "" where nothing is priced."""
        if not self.label:
            return ""
        from toto.quota import rates

        return f"{rates.significant(self.amount)} {self.label}"

    def as_dict(self) -> dict:
        return {
            "amount": text_of(self.amount), "text": text_of(self.text),
            "image": text_of(self.image), "affordable": self.affordable,
            "balance": text_of(self.balance) if self.balance is not None else None,
            "display": self.display,
        }


def quote(user, *, text_bytes: int, image_bytes: int) -> Quote:
    """What a post of these sizes would cost ``user`` now. Nothing is stored
    and nothing is charged; the amounts are the ones ``settle_post`` would
    post, because the ledger's own calculator works them out."""
    billed = lines(text_bytes, image_bytes)
    answer = ledger_quote(user, price_for(user, APP), _charges(billed), metrics=metrics.ALL)
    if answer is None:
        return Quote(Decimal(0), Decimal(0), True, None)
    amounts = {row["metric_code"]: row["amount"] for row in answer["lines"]}
    totals = answer["totals"]
    # Both prices are in one pool's asset on a seeded platform; were staff to
    # price them apart there would be no one balance to name.
    one = totals[0] if len(totals) == 1 else None
    return Quote(
        text=amounts.get(TEXT_KB, Decimal(0)), image=amounts.get(IMAGE_KB, Decimal(0)),
        affordable=bool(answer["affordable"]),
        balance=one["balance"] if one is not None else None,
        label=one["label"] if one is not None else "",
    )


def prices() -> dict:
    """The list price of a kilobyte of text and of image, for the page:
    ``{"text_kb", "image_kb"}`` as decimal strings, "0" where one has no
    price."""
    from toto.quota import rates

    card = rates.rate_card()
    out = {}
    for name, code in (("text_kb", TEXT_KB), ("image_kb", IMAGE_KB)):
        row = card.get(code) or {}
        price = Decimal(str(row.get("price_display") or 0))
        per = Decimal(str(row.get("unit_quantity") or 1))
        out[name] = text_of(price if per == 1 else price / per)
    return out


def afford_post(user, *, text_bytes: int, image_bytes: int) -> None:
    """Refuse a post ``user`` could not be charged for, before anything is
    stored. Raises ``QuotaExceeded`` (429: the day's cap), ``InArrears``
    (402) or ``InsufficientFunds`` (402: the pool's own sentence, for the
    text and the image together)."""
    from .models import ForumQuotaPolicy

    billed = lines(text_bytes, image_bytes)
    for line in billed:
        check_quota(ForumQuotaPolicy, line.code, line.quantity, user)
    check_funds_all(user, price_for(user, APP), _charges(billed))


def settle_post(user, message, *, text_bytes: int, image_bytes: int) -> None:
    """Charge ``user`` for one stored ``message``: ``text_bytes`` of UTF-8
    text and ``image_bytes`` of image (0 without one).

    Call inside the transaction that stores the message. Per metric: the
    usage event, keyed by the message, in a savepoint of its own (a key that
    is already there rolls that savepoint back alone and means this message
    was settled before: nothing is charged again); then the ledger's charge.
    The ledger's refusal is raised, and rolls the caller's transaction back.
    """
    from .models import ForumUsageEvent

    tariff = price_for(user, APP)
    source = {"source_type": SOURCE_TYPE, "source_id": str(message.id)}
    for line in lines(text_bytes, image_bytes):
        try:
            with transaction.atomic():
                ForumUsageEvent.objects.create(
                    metric_code=line.code, quantity=line.quantity, unit=line.unit,
                    user=user, source_label=LABEL[line.code],
                    idempotency_key=f"{line.code}:{message.id}",
                    metadata={"bytes": line.size}, occurred_at=timezone.now(), **source)
        except IntegrityError:
            continue
        charge(user, tariff, line.code, line.quantity, unit=line.unit,
               description=LABEL[line.code], **source)
