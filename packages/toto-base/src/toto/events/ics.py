"""Reading an iCalendar file (``.ics``, RFC 5545) into events to import
(2026-10-12; the owner: "look into morion and allow importing .ics file").

The decisions are Morion's reader's (``apps/morion/src/calendar/ics.ts``),
made again for an import, which keeps one row per meeting and has no viewer:

* times become INSTANTS: a time in UTC as written; one with a TZID in that
  zone (``zoneinfo``); a floating one, and one whose TZID nobody knows, in
  the importer's own zone, and the unknown zone is counted;
* an all-day meeting runs from its first midnight to the midnight after its
  last day, in the importer's zone;
* a repeating meeting is NOT expanded: its first occurrence is kept and the
  series is counted, so the answer can say so;
* tasks, journal entries and free/busy blocks are counted and left out;
  reminders, attendees and attachments are not read at all.

Parsing only, with the standard library: nothing here writes or fetches.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: Larger than this is refused before it is read.
MAX_BYTES = 2 * 1024 * 1024
#: Meetings taken from one file; the rest are counted as skipped.
MAX_EVENTS = 500

LEFT_OUT = ("VTODO", "VJOURNAL", "VFREEBUSY")


class CalendarError(ValueError):
    """Why a text could not be read as a calendar: ``code`` is "empty",
    "too_large" or "not_a_calendar"."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class Meeting:
    title: str
    start: datetime
    end: datetime
    description: str = ""
    place: str = ""
    all_day: bool = False
    repeats: bool = False


@dataclass
class Parsed:
    meetings: list = field(default_factory=list)
    repeating: int = 0          # series kept as their first occurrence
    unknown_zones: int = 0      # times whose TZID nobody knows, read in the importer's zone
    without_time: int = 0       # meetings with no usable start: left out
    left_out: int = 0           # tasks, journal entries, free/busy
    over_the_cap: int = 0       # meetings past MAX_EVENTS


def _unfold(text: str) -> list:
    """Content lines: a line starting with a space or a tab continues the
    one before it (RFC 5545, 3.1)."""
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw.strip():
            lines.append(raw)
    return lines


def _split(line: str):
    """``NAME;PARAM=x:value`` to ``(NAME, {PARAM: x}, value)``. A colon
    inside a quoted parameter value does not end the name part."""
    quoted = False
    for at, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif char == ":" and not quoted:
            head, value = line[:at], line[at + 1:]
            break
    else:
        return line.upper(), {}, ""
    name, *params = head.split(";")
    found = {}
    for param in params:
        key, _, val = param.partition("=")
        found[key.strip().upper()] = val.strip().strip('"')
    return name.strip().upper(), found, value


def _text(value: str) -> str:
    """A TEXT value with its escapes undone (RFC 5545, 3.3.11)."""
    return re.sub(r"\\([nN,;\\])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value).strip()


def _when(params: dict, value: str, own_zone, counts: Parsed):
    """``(instant or date, is_date)`` for a DTSTART/DTEND, or ``(None, False)``."""
    value = value.strip()
    try:
        if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", value):
            return datetime.strptime(value[:8], "%Y%m%d").date(), True
        utc = value.endswith(("Z", "z"))
        naive = datetime.strptime(value.rstrip("Zz"), "%Y%m%dT%H%M%S")
    except ValueError:
        return None, False
    if utc:
        return naive.replace(tzinfo=dt_timezone.utc), False
    zone = own_zone
    tzid = params.get("TZID", "")
    if tzid:
        try:
            zone = ZoneInfo(tzid)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            counts.unknown_zones += 1
    return naive.replace(tzinfo=zone), False


def parse(data, own_zone=None) -> Parsed:
    """The meetings of an ``.ics`` text (``str`` or ``bytes``). ``own_zone``
    is the importer's zone (a tzinfo), for floating and all-day times."""
    if isinstance(data, (bytes, bytearray)):
        if len(data) > MAX_BYTES:
            raise CalendarError("too_large")
        data = bytes(data).decode("utf-8-sig", errors="replace")
    elif len(data.encode("utf-8", errors="ignore")) > MAX_BYTES:
        raise CalendarError("too_large")
    if not data.strip():
        raise CalendarError("empty")
    lines = _unfold(data)
    if not any(line.strip().upper() == "BEGIN:VCALENDAR" for line in lines):
        raise CalendarError("not_a_calendar")
    own_zone = own_zone or dt_timezone.utc
    parsed = Parsed()
    stack, event = [], None
    for line in lines:
        name, params, value = _split(line)
        if name == "BEGIN":
            kind = value.strip().upper()
            stack.append(kind)
            if kind == "VEVENT" and event is None:
                event = {}
            elif kind in LEFT_OUT:
                parsed.left_out += 1
            continue
        if name == "END":
            kind = value.strip().upper()
            if stack and stack[-1] == kind:
                stack.pop()
            if kind == "VEVENT" and event is not None:
                _keep(event, own_zone, parsed)
                event = None
            continue
        # Only the meeting's own lines: not its reminders' (VALARM has a
        # DESCRIPTION too).
        if event is not None and stack and stack[-1] == "VEVENT" and name not in event:
            event[name] = (params, value)
    return parsed


def _keep(event: dict, own_zone, parsed: Parsed) -> None:
    if "DTSTART" not in event:
        parsed.without_time += 1
        return
    start, start_is_date = _when(*event["DTSTART"], own_zone, parsed)
    if start is None:
        parsed.without_time += 1
        return
    end, end_is_date = _when(*event["DTEND"], own_zone, parsed) if "DTEND" in event else (None, False)
    if start_is_date:
        last = end if (end_is_date and isinstance(end, date) and end > start) else start + timedelta(days=1)
        begins = datetime.combine(start, time.min, tzinfo=own_zone)
        ends = datetime.combine(last, time.min, tzinfo=own_zone)
    else:
        begins = start
        ends = end if (isinstance(end, datetime) and not end_is_date and end > start) else None
        if ends is None:
            ends = begins + (_duration(event.get("DURATION", ({}, ""))[1]) or timedelta(hours=1))
    repeats = "RRULE" in event or "RDATE" in event
    if repeats:
        parsed.repeating += 1
    if len(parsed.meetings) >= MAX_EVENTS:
        parsed.over_the_cap += 1
        return
    title = _text(event.get("SUMMARY", ({}, ""))[1])
    parsed.meetings.append(Meeting(
        title=title, start=begins, end=ends,
        description=_text(event.get("DESCRIPTION", ({}, ""))[1]),
        place=_text(event.get("LOCATION", ({}, ""))[1]),
        all_day=start_is_date, repeats=repeats))


def _duration(value: str):
    """An RFC 5545 DURATION (``PT1H30M``, ``P1D``, ``P2W``) or None."""
    match = re.fullmatch(r"\+?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value.strip().upper())
    if not match or not any(match.groups()):
        return None
    weeks, days, hours, minutes, seconds = (int(g or 0) for g in match.groups())
    made = timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds)
    return made if made > timedelta(0) else None
