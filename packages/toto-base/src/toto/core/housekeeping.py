"""The nightly housekeeping (2026-10-01, RODO): what the platform holds about
a person only for as long as it needs it.

    run(*, source="beat") -> dict     # the counts, which are also the record

Run by ``tasks.nightly_housekeeping`` on the beat (``toto.schedules``,
``housekeeping=True``; zenobia switches it on unless ``NIGHTLY_HOUSEKEEPING=0``).
Three steps, each on its own — one that fails never stops the others:

1. **Expired sessions** — Django's own ``clearsessions`` takes them out of
   the session store.
2. **Sign-in rows whose session is gone** — ``user_sessions.prune_dead``: the
   ``UserSession`` rows (an address and a browser each) of sessions that
   expired, were cleared or were ended elsewhere, for every member at once.
   Before this they went only when the member opened their Sessions list.
3. **Lapsed membership applications** — where the socialhub is installed,
   ``toto.socialhub.applications.prune``: applications whose week ran out
   more than ``SOCIALHUB_EXPIRED_APPLICATION_DAYS`` (30) days ago, deleted
   with the never-used accounts they made — never one whose applicant got
   in, never an account that holds anything (``erase_user``'s walk proves it).

**On the chain:** ONE ``PRIVACY.HOUSEKEEPING`` record per run, the system's,
with counts and nothing else — no address, username or e-mail: a record of
who was pruned would keep for ever what the pruning removed. A step that
failed is named in ``failed`` with its exception's class, and the record is
then ``success=False``. Idempotent: a second run the same night finds
nothing due and records its zeros.
"""

from __future__ import annotations

import io
import logging

from django.apps import apps
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from toto.core import user_sessions

log = logging.getLogger("toto.core.housekeeping")


def clear_sessions():
    """Django's ``clearsessions``. Answers how many went where the store is a
    table that can be counted (the db engines), ``None`` elsewhere."""
    from django.core.management import call_command

    counted = None
    if settings.SESSION_ENGINE in user_sessions.LISTABLE_ENGINES:
        from django.contrib.sessions.models import Session

        counted = Session.objects.filter(expire_date__lt=timezone.now()).count()
    errors = io.StringIO()
    call_command("clearsessions", stdout=io.StringIO(), stderr=errors)
    if errors.getvalue().strip():
        # An engine that cannot clear says so on stderr rather than raising.
        log.warning("housekeeping: %s", errors.getvalue().strip())
    return counted


def _step(name, work, failed: dict):
    try:
        return work()
    except Exception as exc:  # noqa: BLE001 - the other steps still run
        failed[name] = type(exc).__name__
        log.exception("housekeeping: %s failed", name)
        return None


def run(*, source: str = "beat") -> dict:
    """One night's housekeeping; the counts it recorded."""
    failed: dict = {}
    result = {
        "sessions_cleared": _step("sessions", clear_sessions, failed),
        "session_rows_dropped": _step("session_rows", user_sessions.prune_dead, failed),
    }
    if apps.is_installed("toto.socialhub"):
        from toto.socialhub import applications

        pruned = _step("applications", applications.prune, failed) or {}
        result.update({
            "applications_pruned": pruned.get("pruned"),
            "accounts_deleted": pruned.get("accounts_deleted"),
            "applications_kept": pruned.get("kept"),
            "expired_days": applications.expired_days(),
        })
    result["failed"] = failed
    _record(result, source)
    return result


def _record(result: dict, source: str) -> None:
    if not apps.is_installed("toto.audit"):
        return
    from toto.audit.services import SYSTEM, record

    try:
        with transaction.atomic():
            record("privacy.housekeeping", app_label="core", object_type="core.housekeeping",
                   object_id=timezone.localdate().isoformat(),
                   description="nightly housekeeping", actor_user=SYSTEM, source=source,
                   success=not result["failed"], metadata=result)
    except Exception:  # noqa: BLE001 - the work is done; the log says the record is missing
        log.exception("housekeeping: the run record could not be written")
