"""Geography on the audit chain: who did what, and what it cost.

Each record holds the actor, the community if any, the metric, the amount
and the outcome. A save also holds its link row's id. A search or a route
holds no row id at all.

NEVER the query, a coordinate, a line, a distance or a duration. The chain
is sealed: what is written cannot be taken out again, so what must never be
needed back is left out.

Guarded like ``toto.socialhub.audit``: a host without ``toto.audit`` records
nothing, and a failed insert never breaks the door.
"""

from __future__ import annotations

import logging

from django.apps import apps

log = logging.getLogger(__name__)

APP_LABEL = "geography"

ADDRESS_SAVED = "GEOGRAPHY.ADDRESS.SAVED"
ADDRESS_CLEARED = "GEOGRAPHY.ADDRESS.CLEARED"
HEADQUARTERS_SAVED = "GEOGRAPHY.HEADQUARTERS.SAVED"
HEADQUARTERS_CLEARED = "GEOGRAPHY.HEADQUARTERS.CLEARED"
ZONE_SAVED = "GEOGRAPHY.ZONE.SAVED"
ZONE_CLEARED = "GEOGRAPHY.ZONE.CLEARED"
SEARCH = "GEOGRAPHY.SEARCH"
ROUTE = "GEOGRAPHY.ROUTE"

ACTIONS = (ADDRESS_SAVED, ADDRESS_CLEARED, HEADQUARTERS_SAVED, HEADQUARTERS_CLEARED,
           ZONE_SAVED, ZONE_CLEARED, SEARCH, ROUTE)

#: What a record says the action was, in place of anything about where.
_DESCRIPTION = {
    ADDRESS_SAVED: "A person's point saved",
    ADDRESS_CLEARED: "A person's point removed",
    HEADQUARTERS_SAVED: "A community's headquarters saved",
    HEADQUARTERS_CLEARED: "A community's headquarters removed",
    ZONE_SAVED: "A community's zone saved",
    ZONE_CLEARED: "A community's zone removed",
    SEARCH: "Place search",
    ROUTE: "Route search",
}


def installed() -> bool:
    return apps.is_installed("toto.audit")


def record(action, actor, *, metric="", amount="0", outcome="done", community=None,
           link=None):
    """One record. ``link`` is the link row of a save or a removal (its type
    and id become the record's object); a search or a route passes none."""
    if not installed():
        return None
    from django.db import transaction

    from toto.audit.services import record as write

    metadata = {"metric": metric, "amount": str(amount), "outcome": outcome}
    if community is not None:
        metadata["community"] = community.slug
    object_type, object_id = "", ""
    if link is not None:
        object_type = f"geography.{type(link).__name__.lower()}"
        object_id = str(link.pk)
        metadata["link"] = link.pk
    try:
        with transaction.atomic():      # a failed insert must not poison the caller's
            return write(action, app_label=APP_LABEL, object_type=object_type,
                         object_id=object_id, description=_DESCRIPTION[action],
                         actor_user=actor, metadata=metadata)
    except Exception:  # noqa: BLE001 - the chain never breaks a door
        log.exception("audit: could not record %s", action)
        return None
