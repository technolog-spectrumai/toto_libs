"""Companies on the audit chain (stage 65): every change of a company's ID
number and of a holding, with who made it.

| action | when |
|---|---|
| `COMPANIES.NUMBER.CHANGED` | the ID number is set, changed or cleared: before and after |
| `COMPANIES.HOLDING.RECORDED` | a person's first holding in a company: the quantity |
| `COMPANIES.HOLDING.CHANGED` | its quantity changed: before and after |
| `COMPANIES.HOLDING.REMOVED` | the holding is taken out of the register: the quantity it had |

A holding's record names the company and the holder by their slugs, as the
socialhub's membership records do. Guarded like ``toto.socialhub.audit``: a
host without ``toto.audit`` records nothing, and a record that cannot be
written never fails the change it describes.
"""

from __future__ import annotations

import logging

from django.apps import apps

log = logging.getLogger(__name__)

APP_LABEL = "companies"

NUMBER_CHANGED = "COMPANIES.NUMBER.CHANGED"
HOLDING_RECORDED = "COMPANIES.HOLDING.RECORDED"
HOLDING_CHANGED = "COMPANIES.HOLDING.CHANGED"
HOLDING_REMOVED = "COMPANIES.HOLDING.REMOVED"

#: Every action this app writes.
ALL = (NUMBER_CHANGED, HOLDING_RECORDED, HOLDING_CHANGED, HOLDING_REMOVED)

_DESCRIPTION = {
    NUMBER_CHANGED: "A company's ID number changed",
    HOLDING_RECORDED: "A shareholding recorded",
    HOLDING_CHANGED: "A shareholding's quantity changed",
    HOLDING_REMOVED: "A shareholding removed",
}


def installed() -> bool:
    return apps.is_installed("toto.audit")


def _write(action, actor, *, object_type, object_id, metadata):
    if not installed():
        return None
    from django.db import transaction

    from toto.audit.services import record as write

    try:
        with transaction.atomic():      # a failed insert must not poison the caller's
            return write(action, app_label=APP_LABEL, object_type=object_type,
                         object_id=str(object_id), description=_DESCRIPTION[action],
                         actor_user=actor, metadata=metadata)
    except Exception:  # noqa: BLE001 - the chain never breaks a door
        log.exception("audit: could not record %s", action)
        return None


def number_changed(actor, community, *, before, after):
    return _write(NUMBER_CHANGED, actor, object_type="socialhub.community",
                  object_id=community.pk,
                  metadata={"community": community.slug, "before": before, "after": after})


def holding(action, actor, community, person, *, holding_id, before=None, after=None):
    metadata = {"community": community.slug, "person": person.slug, "holding": holding_id}
    if before is not None:
        metadata["before"] = before
    if after is not None:
        metadata["after"] = after
    return _write(action, actor, object_type="companies.shareholding",
                  object_id=holding_id, metadata=metadata)
