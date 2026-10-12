"""Mana: what it costs, who has how much, what refills it.

Everything that changes a balance goes through :func:`_apply`: one locked
row, one event, and a ``key`` that lets the change happen once. The doors of
the platform never call this module: they call ``toto.quota.charge``, which
answers through it where Discipline is the host's charger.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.apps import apps
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from toto.quota.charge import InsufficientFunds

from . import config
from .models import ZERO, Aura, CommunityAura, ManaAccount, ManaEvent, ManaKind, Sigil

logger = logging.getLogger("toto.discipline")

#: A balance has four decimals.
_STEP = Decimal("0.0001")
#: How far up a community tree an aura is looked for: a guard against a
#: cycle written by hand, not a limit of the design.
MAX_TREE_DEPTH = 20


def enabled() -> bool:
    return apps.is_installed("toto.discipline")


def _amount(value) -> str:
    """A number as a person reads it: no trailing zeros."""
    text = format(Decimal(value).quantize(_STEP), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


class NotEnoughMana(InsufficientFunds):
    """The person's mana does not cover what the action costs.

    A kind of ``toto.quota.charge.InsufficientFunds``, so every door that
    already answers a refused charge answers this one, with ``str(exc)``."""

    status_code = 402

    def __init__(self, needed, have, detail: str = ""):
        # Not the parent's __init__: where the ledger's package is on the path
        # the parent is its refusal, which counts in an asset's base units.
        # What its handlers read is given here in mana.
        Exception.__init__(self, detail)
        self._needed, self._have = Decimal(needed), Decimal(have)
        self.asset_name = "mana"
        self.asset_decimals = 4
        self.needed_base_units = int(self._needed * 10 ** 4)
        self.have_base_units = int(self._have * 10 ** 4)
        self.topup_url = ""
        self.detail = detail

    @property
    def needed_display(self):
        return self._needed

    @property
    def have_display(self):
        return self._have

    @property
    def shortfall_display(self):
        return max(self._needed - self._have, ZERO)

    def __str__(self):
        return self.detail


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------

def price_of(metric_code: str) -> Decimal | None:
    """Mana per unit of the metric, or None where it costs nothing."""
    price = config.load().prices.get(metric_code)
    return price if price else None


def cost(metric_code: str, quantity) -> Decimal:
    price = price_of(metric_code)
    if price is None:
        return ZERO
    return (price * Decimal(str(quantity))).quantize(_STEP, rounding=ROUND_HALF_UP)


def priced_app(app_label: str) -> bool:
    """Does any metric of this app cost mana?"""
    from toto.quota.metrics import registry

    return any(price_of(metric.code) for metric in registry.for_app(app_label))


# ---------------------------------------------------------------------------
# Balances
# ---------------------------------------------------------------------------

def account_for(user, *, lock: bool = False) -> ManaAccount:
    user_id = getattr(user, "pk", user)
    account, _created = ManaAccount.objects.get_or_create(user_id=user_id)
    if lock:
        account = ManaAccount.objects.select_for_update().get(pk=account.pk)
    return account


def balance(user) -> Decimal:
    if user is None or not getattr(user, "pk", None):
        return ZERO
    row = ManaAccount.objects.filter(user_id=user.pk).values_list("balance", flat=True).first()
    return row if row is not None else ZERO


def chip_state(user) -> dict:
    """What the app bar's chip draws: the balance and the cap as whole
    numbers, and how full the bar is (0 to 100). A balance above the cap (a
    grant) is a full bar; with no cap at all the bar is empty."""
    have, cap = balance(user), speed_for(user).cap
    if cap > 0:
        pct = int(min(Decimal(100), (have * 100 / cap).quantize(Decimal(1), rounding=ROUND_HALF_UP)))
    else:
        pct = 0
    return {"amount": int(have), "cap": int(cap), "pct": max(pct, 0)}


def balance_history(user, *, span, step, now=None) -> list:
    """``[(when, balance)]``: what the person had at each ``step`` over the
    last ``span``, the last point being now. Read from the events: a point is
    the balance after the last change at or before it, and nothing (0) before
    the first change there ever was."""
    now = now or timezone.now()
    start = now - span
    before = (ManaEvent.objects.filter(user=user, created_at__lte=start)
              .order_by("-created_at", "-pk").values_list("balance_after", flat=True).first())
    have = before if before is not None else ZERO
    changes = list(ManaEvent.objects.filter(user=user, created_at__gt=start, created_at__lte=now)
                   .order_by("created_at", "pk").values_list("created_at", "balance_after"))
    points, index, at = [], 0, start
    while True:
        at = min(at, now)
        while index < len(changes) and changes[index][0] <= at:
            have = changes[index][1]
            index += 1
        points.append((at, have))
        if at >= now:
            return points
        at += step


def _apply(user, delta, *, kind, key, floor=False, **fields):
    """Change one balance once. ``(event, made)``: the event under ``key``,
    and whether this call made it.

    ``floor``: a debit larger than the balance takes what is there instead of
    being refused (the caller did the work already). Without it the caller
    must have checked: a balance never goes below zero (the constraint)."""
    user_id = getattr(user, "pk", user)
    existing = ManaEvent.objects.filter(key=key).first()
    if existing is not None:
        return existing, False
    try:
        with transaction.atomic():
            account = account_for(user_id, lock=True)
            delta = Decimal(delta).quantize(_STEP, rounding=ROUND_HALF_UP)
            if floor and account.balance + delta < 0:
                fields.setdefault("metadata", {})["short"] = str(-(account.balance + delta))
                delta = -account.balance
            account.balance = account.balance + delta
            account.save(update_fields=["balance", "updated_at"])
            event = ManaEvent.objects.create(user_id=user_id, delta=delta,
                                             balance_after=account.balance, kind=kind,
                                             key=key, **fields)
        return event, True
    except IntegrityError:
        # The same key, written by another process between the look and the
        # insert: that one is the change.
        existing = ManaEvent.objects.filter(key=key).first()
        if existing is None:
            raise
        return existing, False


# ---------------------------------------------------------------------------
# Spending
# ---------------------------------------------------------------------------

def shortfall_sentence(user, needed: Decimal, have: Decimal) -> str:
    sentence = _("Not enough mana: this needs %(needed)s and you have %(have)s.") % {
        "needed": _amount(needed), "have": _amount(have)}
    speed = speed_for(user)
    if needed > speed.cap:
        sentence += " " + _("That is more than regeneration fills to (%(cap)s).") % {
            "cap": _amount(speed.cap)}
    elif speed.rate > 0:
        hours = max(1, math.ceil((needed - have) / speed.rate))
        sentence += " " + _("It refills %(rate)s an hour: enough again in about %(hours)s h.") % {
            "rate": _amount(speed.rate), "hours": hours}
    else:
        sentence += " " + _("Nothing refills it for you here.")
    from .sources import ManaSource

    for source in ManaSource.all():
        if source.available(user):
            sentence += " " + str(source.describe(user))
    return sentence


def check_all(user, charges) -> None:
    """Raise :class:`NotEnoughMana` unless the person can pay for all of
    ``charges`` (``[(metric_code, quantity, unit), ...]``) together."""
    needed = sum((cost(code, quantity) for code, quantity, *_rest in charges), ZERO)
    if needed <= 0:
        return
    have = balance(user)
    if have < needed:
        raise NotEnoughMana(needed, have, shortfall_sentence(user, needed, have))


def check(user, metric_code: str, quantity) -> None:
    check_all(user, [(metric_code, quantity, "")])


def spend(user, metric_code: str, quantity, *, key: str = "", strict: bool = True,
          unit: str = "", source_type: str = "", source_id="", reason: str = ""):
    """Take what the action costs. None where it costs nothing.

    ``key`` makes a retried call take it once; without one every call is its
    own spend. ``strict=False`` is for a door that checked before it did the
    work: the work is done, so a balance that fell short meanwhile gives what
    it has (the event says how much was missing)."""
    amount = cost(metric_code, quantity)
    if amount <= 0:
        return None
    key = key or f"spend:{uuid.uuid4().hex}"
    fields = {"metric_code": metric_code, "quantity": Decimal(str(quantity)), "unit": unit,
              "source_type": source_type, "source_id": str(source_id or ""),
              "reason": str(reason)[:255]}
    if strict:
        with transaction.atomic():
            existing = ManaEvent.objects.filter(key=key).first()
            if existing is not None:
                return existing
            account = account_for(user, lock=True)
            if account.balance < amount:
                raise NotEnoughMana(amount, account.balance,
                                    shortfall_sentence(user, amount, account.balance))
            event, _made = _apply(user, -amount, kind=ManaKind.SPEND, key=key, **fields)
            return event
    event, _made = _apply(user, -amount, kind=ManaKind.SPEND, key=key, floor=True, **fields)
    return event


def refund(event, *, reason: str = ""):
    """Give a spend back, once. None when there is nothing to give back."""
    if isinstance(event, str):
        event = ManaEvent.objects.filter(key=event).first()
    if event is None or event.kind != ManaKind.SPEND or event.delta >= 0:
        return None
    back, _made = _apply(event.user_id, -event.delta, kind=ManaKind.REFUND,
                         key=f"refund:{event.key}"[:190], metric_code=event.metric_code,
                         quantity=event.quantity, unit=event.unit,
                         source_type=event.source_type, source_id=event.source_id,
                         reason=str(reason)[:255])
    return back


def refund_for(source_type: str, source_id, metric_code: str = "", *, reason: str = ""):
    """Give back the newest spend made for one row that was not given back."""
    spends = ManaEvent.objects.filter(kind=ManaKind.SPEND, source_type=source_type,
                                      source_id=str(source_id))
    if metric_code:
        spends = spends.filter(metric_code=metric_code)
    for event in spends.order_by("-created_at", "-pk")[:20]:
        if not ManaEvent.objects.filter(key=f"refund:{event.key}"[:190]).exists():
            return refund(event, reason=reason)
    return None


# ---------------------------------------------------------------------------
# Putting mana on an account
# ---------------------------------------------------------------------------

def grant(user, amount, *, reason: str = "", by=None, key: str = "", kind=ManaKind.GRANT):
    """Put ``amount`` on the person's account, whole: the cap bounds
    regeneration, not what somebody gives or buys."""
    amount = Decimal(str(amount)).quantize(_STEP, rounding=ROUND_HALF_UP)
    if amount <= 0:
        raise ValueError("a grant is more than zero")
    event, made = _apply(user, amount, kind=kind, key=key or f"{kind}:{uuid.uuid4().hex}",
                         reason=str(reason)[:255], by=by)
    if made and kind == ManaKind.GRANT:
        from . import audit

        audit.mana_granted(event)
    return event


def signup(user):
    """A new account's first balance, once."""
    account_for(user)
    amount = config.load().signup
    if not amount:
        return None
    return grant(user, amount, key=f"signup:{user.pk}", kind=ManaKind.SIGNUP)


# ---------------------------------------------------------------------------
# Speed: the aura of a community, and the sigils held
# ---------------------------------------------------------------------------

@dataclass
class Speed:
    """How fast one person's mana refills, and what from."""

    aura: Aura | None = None
    #: The community the aura comes through (its id; ``community`` once asked).
    community_id: int | None = None
    aura_rate: Decimal = ZERO
    sigils: list = field(default_factory=list)
    rate: Decimal = ZERO
    cap: Decimal = ZERO

    @property
    def community(self):
        if self.community_id is None:
            return None
        from toto.socialhub.models import Community

        return Community.objects.filter(pk=self.community_id).first()

    def parts(self) -> dict:
        return {"aura": str(self.aura_rate),
                "sigils": {str(sigil.uuid): str(sigil.regen_per_hour) for sigil in self.sigils}}


def speeds(user_ids) -> dict:
    """``{user id: Speed}`` for these people, in a handful of queries.

    The aura: the fastest among the auras of the person's communities and of
    every community above them. The sigils: every active one they hold, each
    adding its own rate. The cap: the aura's, or the platform's."""
    from toto.people.models import Person
    from toto.socialhub.models import Community

    numbers = config.load()
    user_ids = list(user_ids)
    auras = {link.community_id: link.aura
             for link in CommunityAura.objects.select_related("aura")}
    held = {}
    for sigil in Sigil.objects.filter(active=True, holder_id__in=user_ids).order_by("name", "pk"):
        held.setdefault(sigil.holder_id, []).append(sigil)

    member_of = {}
    if auras:
        people = dict(Person.objects.filter(user_id__in=user_ids).values_list("pk", "user_id"))
        through = Person.communities.through
        for person_id, community_id in through.objects.filter(
                person_id__in=people).values_list("person_id", "community_id"):
            member_of.setdefault(people[person_id], set()).add(community_id)
    parents = dict(Community.objects.values_list("pk", "parent_id")) if member_of else {}

    table = {}
    for user_id in user_ids:
        best, through_id = None, None
        for community_id in sorted(member_of.get(user_id, ())):
            at, depth = community_id, 0
            while at and depth <= MAX_TREE_DEPTH:
                aura = auras.get(at)
                if aura is not None and (best is None or aura.regen_per_hour > best.regen_per_hour):
                    best, through_id = aura, community_id
                at, depth = parents.get(at), depth + 1
        sigils = held.get(user_id, [])
        aura_rate = best.regen_per_hour if best is not None else numbers.default_regen_per_hour
        table[user_id] = Speed(
            aura=best, community_id=through_id, aura_rate=aura_rate, sigils=sigils,
            rate=aura_rate + sum((sigil.regen_per_hour for sigil in sigils), ZERO),
            cap=best.cap if best is not None and best.cap is not None else numbers.cap)
    return table


def speed_for(user) -> Speed:
    return speeds([user.pk])[user.pk]


# ---------------------------------------------------------------------------
# The hourly regeneration
# ---------------------------------------------------------------------------

@dataclass
class RunReport:
    label: str
    #: Accounts that got mana this run.
    paid: int = 0
    #: At their cap already.
    full: int = 0
    #: With nothing that regenerates.
    idle: int = 0
    #: Regenerated for this hour by an earlier run.
    done: int = 0
    total: Decimal = ZERO

    def __str__(self):
        return (f"{self.label}: {self.paid} refilled ({_amount(self.total)} mana), "
                f"{self.full} full, {self.idle} with nothing regenerating, "
                f"{self.done} already done")


def hour_label(at=None) -> str:
    from datetime import timezone as dt_timezone

    return (at or timezone.now()).astimezone(dt_timezone.utc).strftime("%Y%m%d%H")


def regenerate_hour(*, at=None) -> RunReport:
    """Every active person's hour of regeneration: their aura and their
    sigils, up to their cap. Once an hour a person, whoever calls it and how
    often; an hour that was missed is not made up."""
    from django.contrib.auth import get_user_model

    label = hour_label(at)
    report = RunReport(label=label)
    user_ids = list(get_user_model().objects.filter(is_active=True).values_list("pk", flat=True))
    balances = dict(ManaAccount.objects.filter(user_id__in=user_ids)
                    .values_list("user_id", "balance"))
    for user_id, speed in speeds(user_ids).items():
        if speed.rate <= 0:
            report.idle += 1
            continue
        if balances.get(user_id, ZERO) >= speed.cap:
            report.full += 1
            continue
        key = f"hourly:{label}:{user_id}"
        try:
            with transaction.atomic():
                if ManaEvent.objects.filter(key=key).exists():
                    report.done += 1
                    continue
                account = account_for(user_id, lock=True)
                room = speed.cap - account.balance
                if room <= 0:
                    report.full += 1
                    continue
                event, made = _apply(user_id, min(speed.rate, room), kind=ManaKind.REGEN,
                                     key=key, metadata=speed.parts())
        except Exception:  # noqa: BLE001 - one account never stops the hour
            logger.exception("discipline: regeneration of user %s failed", user_id)
            continue
        if made:
            report.paid += 1
            report.total += event.delta
        else:
            report.done += 1
    return report


# ---------------------------------------------------------------------------
# Auras
# ---------------------------------------------------------------------------

def set_community_aura(community, aura, *, by=None):
    """Give a community this aura, or none (``aura=None``)."""
    from . import audit

    link = CommunityAura.objects.filter(community=community).select_related("aura").first()
    before = link.aura if link is not None else None
    if before == aura:
        return False
    if aura is None:
        link.delete()
    elif link is None:
        CommunityAura.objects.create(community=community, aura=aura)
    else:
        link.aura = aura
        link.save(update_fields=["aura"])
    audit.aura_set(community, before, aura, by=by)
    return True
