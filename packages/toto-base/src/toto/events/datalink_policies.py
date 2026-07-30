"""The calendar. Every reference here points at Person, never at an account.

Worth noticing, because it is what makes this stage possible at all under the
"no accounts" rule: `ScheduledEvent.owner`, `organizers`, `EventInvite.person` and
`Availability.person` all target ``people.Person``. Nothing in events points at
``auth.User``. The `organizers` M2M is permission-bearing — it decides who may invite —
so dropping it would silently narrow the receiver's permissions.
"""
from toto.datalink.registry import (
    IDENTITY_UID,
    STAGE_EVENTS,
    SyncPolicy,
    register,
)

register(SyncPolicy(
    "events.EventCategory", stage=STAGE_EVENTS, identity=IDENTITY_UID,
    unique_guards=(("name",),),
    fields=("name", "description"), bulk_safe=True,
))

register(SyncPolicy(
    "events.ScheduledEvent", stage=STAGE_EVENTS, identity=IDENTITY_UID,
    fields=(
        "title", "description", "start_time", "end_time", "category",
        "public", "owner", "address", "capacity", "requires_registration",
    ),
    m2m=("organizers",), m2m_stage=STAGE_EVENTS,
    bulk_safe=True,
    notes=(
        "Its primary key is a UUID, and that UUID is NOT the identity — `uid` is. The "
        "pk never travels: a UUID pk is still per-instance, since two instances "
        "generate different ones for the same event."
    ),
))

register(SyncPolicy(
    "events.EventInvite", stage=STAGE_EVENTS, identity=IDENTITY_UID,
    unique_guards=(("event", "person"),),
    fields=("event", "person", "status", "note", "responded_at"),
    bulk_safe=True,
    notes=(
        "`event` and `person` are both NOT NULL, so an invite whose event or person "
        "failed to resolve is withheld rather than orphaned. `sent_at` is auto_now_add "
        "and cannot travel."
    ),
))

register(SyncPolicy(
    "events.Availability", stage=STAGE_EVENTS, identity=IDENTITY_UID,
    fields=(
        "person", "start_time", "end_time", "availability_type",
        "reason", "blocks_scheduling",
    ),
    bulk_safe=True,
    notes="Unbounded in practice — one row per busy block per person.",
))
