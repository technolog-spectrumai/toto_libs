"""The charged-door rule: one for every charged door of this app.

``op`` is a UUID the browser mints once per deliberate press and repeats on a
retry. A charged door without one answers 400.

    known(user, op, body)     a replay (True), a fresh op (False), or 409
    afford(user, metric)      quota cap and mana, before any work: 402 / 429
    ... the work: the provider is asked, or the rows are made ...
    settle(user, metric, op, body, label, save=…)
                              in ONE transaction: the rows (``save``), the
                              usage event, and the charge only if the event
                              was written

THE KEY of a usage event is ``<metric>:<user>:<op>`` (the metric's code
starts with ``geography.``). Its ``metadata`` holds ``{"request": <hex>}``
and nothing else: a keyed digest (``salted_hmac``, the server's secret key)
over the user, the op and the normalised body. It cannot be read back, and
two events never share one, since the op is in it.

A KNOWN OP with the same digest, within ``REPLAY_SECONDS`` of its event, is a
replay: the door answers again and charges nothing. A known op with another
digest, or after that time, is 409 and serves nothing: the key the browser
minted bought one request, not a free pass (``quota.record_usage`` answers
None for any known key whatever the request was, so the binding is here).

A FRESH OP is checked for funds first, so a member who cannot pay is told
before any provider is asked and before anything is written. If the ledger
refuses after the work (another request spent the mana meanwhile), the
transaction rolls back: no rows, no event, and the door says 402 and
withholds the answer.

THE EVENT is written in its own savepoint and a concurrent duplicate is
caught outside it. ``record_usage`` swallows the uniqueness error inside the
caller's ``atomic()``, and Postgres then refuses the rest of the block; so
the row is created here, the savepoint rolls back alone, and the loser
leaves the outer block, which undoes its rows.

A LOST RACE is judged as a known op is. Two requests sent at once under one
op both pass ``known`` (neither event is there yet) and both do their work;
the second meets the key at its own insert. ``settle`` then reads the event
that holds the key and compares digests: the same request, within the replay
time, is ``Duplicate`` and the door answers it as a replay, charged nothing;
another request, or no event to compare with, is 409 and nothing is served.
So ``Duplicate`` only ever means "this very request, settled by another".

Imports only the quota gateway, never toto.mana or toto.tariffs: a host with
no economy runs the same doors for free under the same caps.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.translation import gettext as _

from toto.quota.api import check_quota
from toto.quota.charge import charge, check_funds, price_for

from . import metrics

#: How long the same request may be repeated free under its op.
REPLAY_SECONDS = 600

_SALT = "toto.geography.charging"


class Refusal(Exception):
    """A door's own refusal. ``status_code`` and a sentence for the member."""

    def __init__(self, message, status_code, retry_after=None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class Duplicate(Exception):
    """This very request was settled by another one first (the same op, the
    same digest, within the replay time); nothing of this one was kept."""


class _Taken(Exception):
    """The op's key is held by an event another request wrote."""


def _used() -> Refusal:
    return Refusal(_("This request was already used. Press the button again to send a new one."),
                   409)


def clean_op(value) -> str:
    """The op as a canonical UUID string, or 400."""
    try:
        if not isinstance(value, str):
            raise ValueError
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise Refusal(_("This request carries no operation id. Reload the page and try again."),
                      400) from None


def digest(user, op, body) -> str:
    """The keyed digest that binds an op to one request of one member."""
    material = json.dumps([user.pk, op, body], sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True)
    return salted_hmac(_SALT, material, algorithm="sha256").hexdigest()


def _key(metric, user, op) -> str:
    return f"{metric}:{user.pk}:{op}"


def _replays(event, request) -> bool:
    """Is ``event`` the event of the request with the digest ``request``,
    and young enough to be answered again?"""
    same = (event.metadata or {}).get("request") == request
    fresh = timezone.now() - event.occurred_at <= timedelta(seconds=REPLAY_SECONDS)
    return same and fresh


def known(user, op, body) -> bool:
    """Is ``op`` a replay of this very request? False for a fresh op; 409 for
    an op that was used for another request, or too long ago."""
    from .models import GeographyUsageEvent

    event = (GeographyUsageEvent.objects
             .filter(idempotency_key__in=[_key(metric, user, op) for metric in metrics.ALL])
             .first())
    if event is None:
        return False
    if _replays(event, digest(user, op, body)):
        return True
    raise _used()


def afford(user, metric, quantity=1) -> None:
    """Raise QuotaExceeded (429), InArrears (402) or InsufficientFunds (402)
    if the action would be refused. Stores nothing and asks nobody."""
    from .models import GeographyQuotaPolicy

    check_quota(GeographyQuotaPolicy, metric, quantity, user)
    check_funds(user, price_for(user, metrics.APP), metric, quantity,
                metrics.UNIT[metric])


def settle(user, metric, op, body, label, save=None):
    """Keep and charge one action that succeeded.

    ``save()`` writes the rows of a save and its result is returned beside
    what the ledger posted: ``(result, charged)``, where ``charged`` is the
    ledger's answer (None on a host with no price for the metric). ``label``
    names the kind of action and nothing else. With nothing kept, raises the
    ledger's refusal; or, where another request took the op's key first,
    ``Duplicate`` (it was this very request) or the 409 (it was another)."""
    from .models import GeographyUsageEvent

    source = {"source_type": metric, "source_id": op}
    key, request = _key(metric, user, op), digest(user, op, body)
    try:
        with transaction.atomic():
            result = save() if save is not None else None
            try:
                with transaction.atomic():
                    GeographyUsageEvent.objects.create(
                        metric_code=metric, quantity=1, unit=metrics.UNIT[metric],
                        user=user, source_label=label, idempotency_key=key,
                        metadata={"request": request},
                        occurred_at=timezone.now(), **source)
            except IntegrityError:
                raise _Taken() from None
            charged = charge(user, price_for(user, metrics.APP), metric, 1,
                             unit=metrics.UNIT[metric], description=label, **source)
    except _Taken:
        # Out of the block: this request's own rows are undone. The key
        # went to the request whose event holds it; what that request was
        # is in the event, and only the same one is a replay.
        event = GeographyUsageEvent.objects.filter(idempotency_key=key).first()
        if event is not None and _replays(event, request):
            raise Duplicate() from None
        raise _used() from None
    return result, charged


def amount_of(charged) -> str:
    """What the ledger took, in the asset's base units, as text for the audit
    record ("0" where nothing is priced)."""
    if not charged:
        return "0"
    record = charged[0] if isinstance(charged, (tuple, list)) else charged
    try:
        return str(sum(row.amount_base_units for row in record.charges.all()))
    except Exception:  # noqa: BLE001 - another ledger's record: no amount to name
        return ""
