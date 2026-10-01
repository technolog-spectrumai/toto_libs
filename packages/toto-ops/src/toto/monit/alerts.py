"""Being told when something breaks: the record checks on a schedule, and mail.

Until 2026-10-01 ``record.run_checks()`` ran only when a superuser opened the
Database page, and nothing told anyone anything. Now ``monit_alert_checks``
(tasks.py; every ALERT_CHECK_MINUTES on the beat, five by default) runs the
same checks in the worker, keeps each one's last verdict in ``CheckState``, and
mails ALERT_EMAILS when a verdict CHANGES:

- a check that goes bad is mailed once (kind ``check_alert``);
- one that stays FAILING is mailed again every ALERT_REMIND_HOURS (six by
  default; 0 never) — a warning is said once and not repeated;
- one that gets worse (unknown → warn → fail) is mailed again; one that gets
  better without coming back (fail → warn) is not, and keeps its reminder
  clock;
- one that comes back (ok, or off) is mailed once more (``check_recovered``).

A check that stays as it was is silence. Bad means FAIL, WARN, and UNKNOWN — a
probe that could not run is a monitor gone blind, which is worth one mail. OFF
is a check this host does not run, as quiet as OK.

A mail no address took is not counted as sent, so the next run tries again.
With ALERT_EMAILS empty the states are still kept and nothing is mailed; a
check still bad when an address is added is mailed then. Every mail leaves
through ``toto.core.notices.send_notice``, the one seam notices use.

WHAT THIS CANNOT DO, said plainly: the mail is sent BY the server, so it
cannot report the server itself being down. No power, no network, a stopped
worker or beat, a database that cannot be reached (the states live in it) —
each ends in silence, not in a mail. Only something outside the server can
notice those.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import record

log = logging.getLogger("toto.monit.alerts")

#: How bad each verdict is, for "did it get worse".
RANK = {record.OK: 0, record.OFF: 0, record.UNKNOWN: 1, record.WARN: 2,
        record.FAIL: 3}

PROBLEM, REMINDER, RECOVERED = "problem", "reminder", "recovered"


def _rank(status: str) -> int:
    # A verdict this table does not know is not silently fine.
    return RANK.get(status, RANK[record.UNKNOWN])


def recipients() -> list[str]:
    """ALERT_EMAILS as a list without blanks or repeats (a list, or one
    comma-separated string)."""
    raw = getattr(settings, "ALERT_EMAILS", None) or []
    if isinstance(raw, str):
        raw = raw.split(",")
    out = []
    for address in raw:
        address = str(address).strip()
        if address and address not in out:
            out.append(address)
    return out


def remind_hours() -> int:
    try:
        return max(0, int(getattr(settings, "ALERT_REMIND_HOURS", 6)))
    except (TypeError, ValueError):
        return 6


def decide(state, status: str, now, remind_after) -> str | None:
    """What one run owes the operators for one check: an event, or None.

    ``state`` already carries this run's ``status``; its ``alerted_*`` fields
    are still what was mailed before.
    """
    rank = _rank(status)
    if rank == 0:
        return RECOVERED if state.alerted_status else None
    if not state.alerted_status or rank > _rank(state.alerted_status):
        return PROBLEM
    if (status == record.FAIL and remind_after is not None
            and state.alerted_at is not None
            and now - state.alerted_at >= remind_after):
        return REMINDER
    return None


def status_url() -> str:
    """The Database page on the platform's own domain, or "" without one."""
    from django.urls import NoReverseMatch, reverse

    domain = str(getattr(settings, "PLATFORM_DOMAIN", "") or "").strip().rstrip("/")
    if not domain:
        return ""
    try:
        path = reverse("monit:status")
    except NoReverseMatch:
        return ""
    base = domain if domain.startswith(("http://", "https://")) else f"https://{domain}"
    return base + path


def _mail(event: str, check, state, to: list[str], hours: int) -> bool:
    """One notice per address; True when at least one address took it."""
    from toto.core.notices import send_notice

    kind = "check_recovered" if event == RECOVERED else "check_alert"
    context = {
        "event": event,
        "check_key": check.key,
        "check_label": str(check.label),
        "status": check.status,
        "summary": str(check.summary),
        "detail": str(check.detail),
        "since": state.since,
        "bad_since": state.bad_since,
        "remind_hours": hours if check.status == record.FAIL else 0,
        "status_url": status_url(),
    }
    taken = sum(1 for address in to
                if send_notice(None, kind, context, to=address))
    if not taken:
        log.warning("alerts: %s about %s reached no address; the next run "
                    "tries again", event, check.key)
    return taken > 0


def run(*, now=None, checks=None) -> dict:
    """One scheduled pass: check, remember, mail what changed.

    ``checks`` replaces ``record.run_checks()`` (tests). Returns, per event,
    how many checks were mailed about.
    """
    from .models import CheckState

    now = now or timezone.now()
    checks = record.run_checks() if checks is None else list(checks)
    to = recipients()
    hours = remind_hours()
    remind_after = timedelta(hours=hours) if hours else None
    sent = {PROBLEM: 0, REMINDER: 0, RECOVERED: 0}

    # The checks ran above, outside any lock — some read a whole tree. The
    # rows are locked only while deciding, mailing and saving, so a run that
    # overlaps the previous one waits for it and then sees what it mailed.
    with transaction.atomic():
        rows = {row.key: row for row in CheckState.objects.select_for_update()
                .filter(key__in=[check.key for check in checks])}
        for check in checks:
            state = rows.get(check.key)
            if state is None:
                state = CheckState(key=check.key, status=check.status, since=now)
                if _rank(check.status):
                    state.bad_since = now
            elif state.status != check.status:
                if _rank(check.status) and not _rank(state.status):
                    state.bad_since = now
                state.status, state.since = check.status, now
            state.label = str(check.label)[:80]
            state.summary = str(check.summary)[:300]
            state.checked_at = now

            event = decide(state, check.status, now, remind_after) if to else None
            if event and _mail(event, check, state, to, hours):
                sent[event] += 1
                if event == RECOVERED:
                    state.alerted_status = ""
                else:
                    if _rank(check.status) > _rank(state.alerted_status or record.OK):
                        state.alerted_status = check.status
                    state.alerted_at = now
            state.save()
    return sent
