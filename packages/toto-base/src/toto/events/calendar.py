"""The calendar payload, built once.

`oya/partials/calendar.html` draws the grid; this produces what it eats. The
pair exists because four different pages now show a calendar — the events
page, a bounty's timeline, a community's and a company's — and before this
each would have built its own `{title, start, end, url}` dicts, which is how
the events page and the orphaned partial drifted apart in the first place.

WHAT IT DOES NOT DO: decide who may see anything. Callers pass a queryset they
have already scoped — `events.access.visible_events` for the viewer, narrowed
again by whatever owns the page. A helper that filtered would be a second
place the access rule lived, which is the failure `access.py` exists to end.
"""

from __future__ import annotations

import json

from django.core.serializers.json import DjangoJSONEncoder
from django.urls import NoReverseMatch, reverse
from django.utils.timezone import localtime


def _url_for(event) -> str:
    """The event's page, or "" where this build does not mount one.

    `reverse` raises rather than returning nothing, and a calendar embedded in
    another app's page must not 500 because `toto.events` is not routed here.
    """
    try:
        return reverse("events:event_detail", args=[event.pk])
    except NoReverseMatch:
        return ""


def calendar_payload(events) -> str:
    """A JSON string for the partial's `data` parameter.

    Times are localised before serialising: FullCalendar reads the offset off
    the string, and handing it UTC would draw every event in the wrong cell
    for anybody east or west of the server.
    """
    return json.dumps(
        {"events": [
            {
                "title": event.title,
                "start": localtime(event.start_time).isoformat(),
                "end": localtime(event.end_time).isoformat(),
                "url": _url_for(event),
            }
            for event in events
        ]},
        cls=DjangoJSONEncoder,
    )


def calendar_colors(context) -> dict:
    """`{background, text}` for the partial, off the decorated theme.

    A helper rather than a literal in four templates: the events page already
    computed this and named the keys `background_light`/`text_light`, which
    read like there is a dark pair somewhere. There is not — FullCalendar is
    given one event colour and the grid itself follows the page's own theme —
    so the shared names drop the suffix and say what they are.
    """
    theme = (context or {}).get("theme") or {}
    colors = theme.get("colors") or {}
    return {
        "background": colors.get("accent-light", "#36A2EB"),
        "text": colors.get("text-main-light", "#000000"),
    }
