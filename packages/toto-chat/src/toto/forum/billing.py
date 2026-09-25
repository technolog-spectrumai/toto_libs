"""Charging the forum: before, a check; after, one atomic entry — never twice.

    check_affordable(user, channel)      quota cap + mana; raises, stores nothing
    with transaction.atomic():
        row = store_message(...)
        settle_message(user, row)        record_usage keyed on the message,
                                         then charge — in the SAME transaction

The ledger posting is itself atomic, so a charge that fails rolls the message
back with it and a message that fails to store is never charged: neither exists
without the other. `record_usage` dedupes on `forum.message:<uuid>`, so a
retried settle cannot charge twice. A pool the check found short refuses the
send with the pool's own sentence and no row — never a negative balance.

Ordinary messages bill SECURITY mana; sealing and room keys bill COMPUTE mana
(toto/mana/colours.py). Rates are Tariff rows seeded once by `ingress_mana`,
overridable with `settings.MANA_PRICES`; `{% price_hint "forum.message" %}`
shows the price beside the Send button.
"""

from __future__ import annotations

from toto.quota.api import check_quota, record_usage
from toto.quota.charge import charge, check_funds, price_for

APP = "forum"
MESSAGE = "forum.message"
ENCRYPT = "forum.encrypt"
ROOM_KEY = "forum.room_key"


def _codes_for(channel):
    return (ENCRYPT,) if channel.is_encrypted else (MESSAGE,)


def check_affordable(user, channel) -> None:
    from .models import ForumQuotaPolicy

    tariff = price_for(user, APP)
    for code in _codes_for(channel):
        check_quota(ForumQuotaPolicy, code, 1, user)
        check_funds(user, tariff, code, 1)


def check_room_key_affordable(user) -> None:
    from .models import ForumQuotaPolicy

    check_quota(ForumQuotaPolicy, ROOM_KEY, 1, user)
    check_funds(user, price_for(user, APP), ROOM_KEY, 1)


def _settle(user, code, source_id, label, unit) -> bool:
    from .models import ForumUsageEvent

    source = {"source_type": "forum.ForumMessage" if code != ROOM_KEY else "forum.ForumChannel",
              "source_id": str(source_id), "source_label": label}
    event = record_usage(ForumUsageEvent, code, 1, user, unit=unit,
                         idempotency_key=f"{code}:{source_id}", **source)
    if event is None:
        return False
    charge(user, price_for(user, APP), code, 1, unit=unit, **source)
    return True


def settle_message(user, message) -> None:
    """Record and charge one stored message. Call inside the transaction that
    stored it; raises (and so rolls it back) if the ledger refuses."""
    if user is None or not getattr(user, "is_authenticated", False):
        return
    code = ENCRYPT if message.is_sealed else MESSAGE
    _settle(user, code, message.id, f"message in {message.channel_id}", "message")


def settle_room_key(user, channel) -> None:
    if user is None or not getattr(user, "is_authenticated", False):
        return
    _settle(user, ROOM_KEY, channel.pk, channel.name, "key")
