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

# Stage 64 (2026-10-06): members' contributions to a community, and the
# comments under them. ``link`` is then the pin, the zone or the comment's
# link row; never a coordinate, an outline or a comment's text.
PIN_CREATED = "GEOGRAPHY.PIN.CREATED"
PIN_EDITED = "GEOGRAPHY.PIN.EDITED"
PIN_DELETED = "GEOGRAPHY.PIN.DELETED"
PIN_HIDDEN = "GEOGRAPHY.PIN.HIDDEN"
PIN_RESTORED = "GEOGRAPHY.PIN.RESTORED"
ZONE_CREATED = "GEOGRAPHY.ZONE.CREATED"
ZONE_EDITED = "GEOGRAPHY.ZONE.EDITED"
ZONE_DELETED = "GEOGRAPHY.ZONE.DELETED"
ZONE_HIDDEN = "GEOGRAPHY.ZONE.HIDDEN"
ZONE_RESTORED = "GEOGRAPHY.ZONE.RESTORED"
COMMENT_CREATED = "GEOGRAPHY.COMMENT.CREATED"
COMMENT_EDITED = "GEOGRAPHY.COMMENT.EDITED"
COMMENT_WITHDRAWN = "GEOGRAPHY.COMMENT.WITHDRAWN"

CONTRIBUTIONS = (PIN_CREATED, PIN_EDITED, PIN_DELETED, PIN_HIDDEN, PIN_RESTORED,
                 ZONE_CREATED, ZONE_EDITED, ZONE_DELETED, ZONE_HIDDEN, ZONE_RESTORED,
                 COMMENT_CREATED, COMMENT_EDITED, COMMENT_WITHDRAWN)

#: Stage 63's eight: the searches, and the saves of a person's point and of
#: a community's headquarters and zone.
ACTIONS = (ADDRESS_SAVED, ADDRESS_CLEARED, HEADQUARTERS_SAVED, HEADQUARTERS_CLEARED,
           ZONE_SAVED, ZONE_CLEARED, SEARCH, ROUTE)

#: Every action this app writes.
ALL = ACTIONS + CONTRIBUTIONS

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
    PIN_CREATED: "A community pin saved",
    PIN_EDITED: "A community pin changed",
    PIN_DELETED: "A community pin deleted",
    PIN_HIDDEN: "A community pin hidden by a moderator",
    PIN_RESTORED: "A community pin shown again",
    ZONE_CREATED: "A community zone saved",
    ZONE_EDITED: "A community zone changed",
    ZONE_DELETED: "A community zone deleted",
    ZONE_HIDDEN: "A community zone hidden by a moderator",
    ZONE_RESTORED: "A community zone shown again",
    COMMENT_CREATED: "A comment under a pin or zone",
    COMMENT_EDITED: "A comment under a pin or zone changed",
    COMMENT_WITHDRAWN: "A comment under a pin or zone withdrawn",
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
