"""A member's sessions, ended from the outside (2026-09-30).

    end_other_sessions(user, keep=request.session.session_key)  -> int

Ends every session that signs ``user`` in except ``keep``: browser cookies
and desktop tokens alike, since a token IS a session key (``toto.api.tokens``).
Used when a member changes their password on My account; the "sign out
everywhere else" button of 32.3 is the same call.

A password change already makes every other session useless — Django and
``toto.api.tokens`` both check the session hash — but only when the session
is next presented; until then the row sits in the store holding a sign-in.
Ending them is the difference between "would be refused" and "is gone".

Django's session table has no user column, so the sessions are found by
reading them: every unexpired row is decoded and its ``_auth_user_id``
compared. That is a scan, bounded by the number of live sessions on the
host, and it is what this module does until 32.3 gives every sign-in a row
that names its account. Only the database-backed engines can be listed; on
another engine nothing is ended here and the hash check does the work, which
the answer (0) says.
"""

from __future__ import annotations

import logging
from importlib import import_module

from django.conf import settings

log = logging.getLogger("toto.core.sessions")

#: The engines whose sessions live in ``django_session`` and can be listed.
LISTABLE_ENGINES = (
    "django.contrib.sessions.backends.db",
    "django.contrib.sessions.backends.cached_db",
)


def _listable() -> bool:
    return settings.SESSION_ENGINE in LISTABLE_ENGINES


def session_keys_for(user) -> list[str]:
    """The keys of every unexpired session signing ``user`` in."""
    from django.contrib.auth import SESSION_KEY
    from django.contrib.sessions.models import Session
    from django.utils import timezone

    if not _listable() or not getattr(user, "pk", None):
        return []
    wanted = str(user.pk)
    keys = []
    rows = Session.objects.filter(expire_date__gt=timezone.now()).only("session_key", "session_data")
    for row in rows.iterator():
        try:
            data = row.get_decoded()
        except Exception:  # noqa: BLE001 - a row that does not decode signs nobody in
            continue
        if str(data.get(SESSION_KEY, "")) == wanted:
            keys.append(row.session_key)
    return keys


def end_other_sessions(user, *, keep: str | None = "") -> int:
    """End every session of ``user`` but ``keep``; the number ended.

    Through the engine's own ``delete`` so a cached copy goes with the row.
    """
    engine = import_module(settings.SESSION_ENGINE)
    ended = 0
    for key in session_keys_for(user):
        if keep and key == keep:
            continue
        try:
            engine.SessionStore(session_key=key).delete(key)
            ended += 1
        except Exception as exc:  # noqa: BLE001 - the hash check still refuses it
            log.warning("sessions: one session of account %s could not be ended (%s)",
                        user.pk, type(exc).__name__)
    return ended
