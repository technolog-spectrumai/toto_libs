"""Discipline on the audit chain.

| action | when |
|---|---|
| `DISCIPLINE.MANA_GRANTED` | an administrator puts mana on a person's account: the amount, the reason's length, the balance after |
| `DISCIPLINE.SIGIL_CREATED` / `_CHANGED` / `_DELETED` | a sigil is made, its name or rate changed (before and after), or deleted: its uuid, name and rate |
| `DISCIPLINE.SIGIL_ASSIGNED` / `_REVOKED` | a sigil is handed to a person, or taken from them |
| `DISCIPLINE.AURA_CREATED` / `_CHANGED` / `_DELETED` | an aura is made, its name, rate or cap changed (before and after), or deleted |
| `DISCIPLINE.AURA_SET` | a community is given an aura, another one, or none |

Sigils and auras are changed in the admin panel alone (``admin.py``), which
calls these as the services do.

Nothing is recorded where ``toto.audit`` is not installed, and a record that
cannot be written never fails the change it describes.
"""

from __future__ import annotations

import logging

from django.apps import apps

log = logging.getLogger("toto.discipline")

APP_LABEL = "discipline"


def _record(action, *, object_type, object_id, description, by=None, **metadata):
    if not apps.is_installed("toto.audit"):
        return None
    from django.db import transaction

    from toto.audit.services import record

    extra = {"actor_user": by} if by is not None else {}
    try:
        with transaction.atomic():
            return record(f"DISCIPLINE.{action}", app_label=APP_LABEL, object_type=object_type,
                          object_id=str(object_id), description=str(description)[:500],
                          metadata=metadata, **extra)
    except Exception:  # noqa: BLE001 - the chain never breaks the change
        log.exception("audit: could not record DISCIPLINE.%s", action)
        return None


def mana_granted(event):
    return _record("MANA_GRANTED", object_type="discipline.manaaccount", object_id=event.user_id,
                   description=str(event.user), by=event.by, user=event.user.get_username(),
                   amount=str(event.delta), balance_after=str(event.balance_after),
                   reason_length=len(event.reason))


def sigil(sigil, action, *, by=None, holder=None, before=None):
    metadata = {"uuid": str(sigil.uuid), "name": sigil.name, "rate": str(sigil.regen_per_hour)}
    if holder is not None:
        metadata["holder"] = holder.get_username()
    if before is not None:
        metadata["before"] = before
    return _record(action, object_type="discipline.sigil", object_id=sigil.pk,
                   description=sigil.name, by=by, **metadata)


def aura(aura, action, *, by=None, before=None):
    metadata = {"slug": aura.slug, "name": aura.name, "rate": str(aura.regen_per_hour),
                "cap": None if aura.cap is None else str(aura.cap)}
    if before is not None:
        metadata["before"] = before
    return _record(action, object_type="discipline.aura", object_id=aura.pk,
                   description=aura.name, by=by, **metadata)


def aura_set(community, before, after, *, by=None):
    return _record("AURA_SET", object_type="socialhub.community", object_id=community.pk,
                   description=community.name, by=by, community=community.slug,
                   before=getattr(before, "slug", None), after=getattr(after, "slug", None))
