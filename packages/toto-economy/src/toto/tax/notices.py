"""Calendar notices for arrears — the authoritative warning channel.

The platform's warning is a ``toto.events`` calendar entry plus an invite
(the invite is what surfaces it under "my invites" and on the profile card).
Email via jess is deliberately not used here: jess can be manually held, and
a warning that a week-long clock is running must not sit in a moderation
queue.

Events are keyed on ``toto.people.Person``, not ``User``, and the bridge can
be missing — callers get ``None`` back in that case and decide what it means
(the arrears machinery lets the clock run anyway, because a missing profile
must not make storage free).

Titles are neutral on purpose: the shared calendar shows every event to any
signed-in user, so "action required" goes in the title and the numbers go in
the description and the invite note. The details page is /tax/.
"""

from __future__ import annotations

from datetime import timedelta

from toto.events.models import EventCategory, EventInvite, ScheduledEvent

CATEGORY_NAME = "Platform notices"


def _person_for(user):
    try:
        return user.community_profile
    except Exception:  # noqa: BLE001 - reverse OneToOne raises when absent
        return None


def _category():
    category, _created = EventCategory.objects.get_or_create(
        name=CATEGORY_NAME,
        defaults={"description": "Automatic notices from the platform."},
    )
    return category


def create_warning_event(user, *, deadline, shortfall=None, asset: str = "",
                         allowance_text: str = "",
                         consequence: str = "") -> ScheduledEvent | None:
    """The one-week warning. Returns the event, or None when the user has no
    Person profile. Sits on the deadline day so the calendar shows the date
    that matters. ``consequence`` states what enforcement means for the levied
    resource; empty keeps the storage-deletion wording."""
    person = _person_for(user)
    if person is None:
        return None

    short_text = f"{shortfall} {asset}".strip() if shortfall is not None else "the amount due"
    if not consequence:
        consequence = (
            "files chosen at random will be permanently deleted from your "
            "storage until it is back within the free allowance"
            f"{f' of {allowance_text}' if allowance_text else ''}. "
            "Deleted files cannot be recovered by anyone"
        )
    description = (
        "A recurring platform fee could not be collected from your "
        f"account (short by {short_text}).\n"
        f"You have until {deadline:%Y-%m-%d} to top up your wallet.\n"
        f"If the fee still cannot be collected by then, {consequence}.\n"
        "The details and this case: /tax/ — top up via "
        "your wallet (ask an administrator, or trade on the bourse)."
    )
    event = ScheduledEvent.objects.create(
        title=f"Account notice — action required by {deadline:%Y-%m-%d}",
        description=description,
        start_time=deadline,
        end_time=deadline + timedelta(hours=1),
        owner=person,
        category=_category(),
        public=False,
    )
    EventInvite.objects.create(
        event=event, person=person,
        note="A platform storage fee is overdue. Top up before the deadline "
             "or stored files will be permanently deleted.",
    )
    return event


def create_enforcement_event(user, *, deleted_count: int, deleted_raw: int,
                             freed_text: str = "",
                             summary: str = "") -> ScheduledEvent | None:
    """The after-the-fact notice: what was shed. ``freed_text`` renders the
    amount in the resource's own unit (a provider's format_raw); ``summary``
    replaces the whole storage-worded sentence. Defaults keep the storage
    wording."""
    person = _person_for(user)
    if person is None:
        return None

    from django.utils import timezone

    now = timezone.now()
    if not freed_text:
        freed_text = f"{deleted_raw / 2 ** 30:.2f} GB"
    title_suffix = "storage reduced"
    if summary:
        title_suffix = "limits reduced"
    else:
        summary = (
            f"{deleted_count} file(s) ({freed_text}) chosen at random were "
            "permanently deleted to bring your storage back within the free "
            "allowance. Deleted files cannot be recovered."
        )
    description = (
        "The unpaid fee deadline passed. "
        f"{summary}\n"
        "Details: /tax/."
    )
    event = ScheduledEvent.objects.create(
        title=f"Account notice — {title_suffix}",
        description=description,
        start_time=now,
        end_time=now + timedelta(hours=1),
        owner=person,
        category=_category(),
        public=False,
    )
    EventInvite.objects.create(
        event=event, person=person,
        note=f"{deleted_count} file(s) were permanently deleted for an unpaid storage fee.",
    )
    return event
