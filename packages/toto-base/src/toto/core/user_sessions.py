"""A member's sessions: listed, watched and ended (2026-09-30).

    sessions_for(user)                                  -> [UserSession, ...]
    end_session(user, session_id, *, request=None)      -> UserSession | None
    end_other_sessions(user, keep=request.session.session_key)  -> int
    session_keys_for(user)                              -> [key, ...]

Django's session table has no user column, so every sign-in writes a
``toto.core.models.UserSession`` row naming its account (``user_logged_in``,
wired in ``CoreConfig.ready``), and the row goes when the session signs out
(``user_logged_out``) or is ended here. Browser cookies and desktop tokens
alike: a token IS a session key (``toto.api.tokens``); its door marks the
request before ``login`` (``mark_token_signin``) so the row says which.

**Ending** deletes the session from the store through the engine's own
``delete`` — a cached copy goes with the row, and a Bearer token made from
that key is refused from its next request — then the row. A password change
on My account ends every other session this way, and so does "sign out
everywhere else". A session from before these rows existed has none until it
is next used (``touch`` writes it); until then only the session hash check
refuses it after a password change, as it did before.

**Last seen** is refreshed by ``touch`` at most every ``SEEN_EVERY`` seconds
per session — a cache marker decides, so an ordinary request writes nothing.
The cookie door calls it from ``UserSessionMiddleware``, the token doors from
``toto.api.tokens``.

**Rows outlive sessions**: one that expired, was cleared by housekeeping or
was re-keyed keeps its row until something looks. ``sessions_for`` asks the
store which keys are still alive and deletes the rows of the dead ones, so
the list never shows a sign-in that no longer works.

**A new sign-in** — a (user agent, address) pair this member has not signed
in from in ``KNOWN_FOR_DAYS`` days — mails them the ``new_sign_in`` notice.
Except the first sign-in an account makes after this date: with nothing
known yet everything is new, and a deploy should not mail every member.
The pairs are kept as hashes only (``KnownSignIn``).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta
from importlib import import_module

from django.conf import settings

log = logging.getLogger("toto.core.sessions")

#: The engines whose sessions live in ``django_session`` and can be listed.
LISTABLE_ENGINES = (
    "django.contrib.sessions.backends.db",
    "django.contrib.sessions.backends.cached_db",
)

#: Seconds between two refreshes of a session's "last seen".
SEEN_EVERY = 300

#: How long a (user agent, address) pair stays known without a sign-in.
KNOWN_FOR_DAYS = 90

#: The request attribute a token door sets before ``login``.
KIND_ATTR = "toto_session_kind"

_UA_MAX = 300


def _models():
    from toto.core.models import KnownSignIn, UserSession

    return UserSession, KnownSignIn


def _now():
    from django.utils import timezone

    return timezone.now()


def _listable() -> bool:
    return settings.SESSION_ENGINE in LISTABLE_ENGINES


def _endable() -> bool:
    """Does the store hold sessions that can be deleted from outside?

    A signed-cookie session lives in the browser alone: nothing here can end
    it, and the session hash check is all there is.
    """
    return settings.SESSION_ENGINE != "django.contrib.sessions.backends.signed_cookies"


def _engine():
    return import_module(settings.SESSION_ENGINE)


def _user_agent(request) -> str:
    if request is None:
        return ""
    return str(request.META.get("HTTP_USER_AGENT", "") or "")[:_UA_MAX]


def _address(request):
    if request is None:
        return None
    from toto.core.client_ip import client_ip

    return client_ip(request) or None


def mark_token_signin(request) -> None:
    """Say, before ``login``, that this sign-in hands out a token."""
    setattr(request, KIND_ATTR, _models()[0].KIND_TOKEN)


# ── Which sessions are alive ───────────────────────────────────────────────


def _alive(keys) -> set[str]:
    """The keys among ``keys`` that still sign someone in."""
    keys = [k for k in keys if k]
    if not keys:
        return set()
    if _listable():
        from django.contrib.sessions.models import Session

        return set(Session.objects.filter(session_key__in=keys, expire_date__gt=_now())
                   .values_list("session_key", flat=True))
    if not _endable():
        # Nothing is stored server-side to ask.
        return set(keys)
    store = _engine().SessionStore()
    alive = set()
    for key in keys:
        try:
            if store.exists(key):
                alive.add(key)
        except Exception:  # noqa: BLE001 - a store that cannot answer keeps the row
            alive.add(key)
    return alive


def sessions_for(user) -> list:
    """The member's live sessions, most recently used first.

    The rows whose session is gone are deleted on the way (see the module
    docstring); the answer holds only sessions that still sign in.
    """
    UserSession, _ = _models()
    if not getattr(user, "pk", None):
        return []
    rows = list(UserSession.objects.filter(user=user).order_by("-last_seen_at", "-pk"))
    alive = _alive([row.session_key for row in rows])
    dead = [row.pk for row in rows if row.session_key not in alive]
    if dead:
        UserSession.objects.filter(pk__in=dead).delete()
    return [row for row in rows if row.session_key in alive]


def session_keys_for(user) -> list[str]:
    """The keys of every live session signing ``user`` in."""
    return [row.session_key for row in sessions_for(user)]


# ── Ending ─────────────────────────────────────────────────────────────────


def _end_key(key: str, user_pk=None) -> bool:
    """Delete one session from the store; did it go?"""
    try:
        _engine().SessionStore(session_key=key).delete(key)
    except Exception as exc:  # noqa: BLE001 - the hash check still refuses it
        log.warning("sessions: one session of account %s could not be ended (%s)",
                    user_pk, type(exc).__name__)
        return False
    from django.apps import apps

    if apps.is_installed("toto.jess"):
        # A mail credential unlocked in that session goes with it, as it
        # does at a sign-out (jess.credentials.on_user_logged_out).
        try:
            from toto.jess.credentials import lock

            lock(key)
        except Exception:  # noqa: BLE001 - the session is ended either way
            pass
    return True


def end_session(user, session_id, *, request=None):
    """End the member's session ``session_id`` (a ``UserSession`` id).

    Only one of ``user``'s own: another member's id, or one that is gone, is
    ``None`` — the caller answers 404 and learns nothing about the id.
    Answers the row (deleted, its fields still readable) when it ended.
    """
    UserSession, _ = _models()
    row = UserSession.objects.filter(user=user, pk=session_id).first()
    if row is None or not _endable():
        return None
    _end_key(row.session_key, user.pk)
    row_pk = row.pk
    UserSession.objects.filter(pk=row_pk).delete()
    row.pk = row_pk
    return row


def end_other_sessions(user, *, keep: str | None = "") -> int:
    """End every live session of ``user`` but ``keep``; the number ended.

    Through the engine's own ``delete`` so a cached copy goes with the row.
    """
    UserSession, _ = _models()
    if not _endable():
        return 0
    ended = 0
    for row in sessions_for(user):
        if keep and row.session_key == keep:
            continue
        if _end_key(row.session_key, user.pk):
            ended += 1
            UserSession.objects.filter(pk=row.pk).delete()
    return ended


def rekey(user, old_key: str | None, new_key: str | None) -> None:
    """A session that changed its key keeps its row (and its first sign-in).

    ``update_session_auth_hash`` cycles the key; without this the row would
    point at a deleted session and the live one would have none until
    ``touch`` wrote a fresh one.
    """
    if not old_key or not new_key or old_key == new_key:
        return
    UserSession, _ = _models()
    try:
        UserSession.objects.filter(user=user, session_key=old_key).update(
            session_key=new_key, last_seen_at=_now())
    except Exception as exc:  # noqa: BLE001 - touch writes a row for it later
        log.warning("sessions: a re-keyed session kept no row (%s)", type(exc).__name__)


# ── Writing rows ───────────────────────────────────────────────────────────


def _record(user, key, request, kind):
    UserSession, _ = _models()
    now = _now()
    # A key signs in afresh (a login over a live session keeps nothing of
    # the old one): the row is replaced, its first sign-in with it.
    UserSession.objects.update_or_create(
        session_key=key,
        defaults={"user": user, "kind": kind, "ip": _address(request),
                  "user_agent": _user_agent(request), "created_at": now,
                  "last_seen_at": now},
    )


def touch(user, key, request, *, kind=None) -> None:
    """Note that ``key`` was used now, at most every ``SEEN_EVERY`` seconds.

    Writes the row when there is none (a session signed in before these rows
    existed). Never raises: a request is never refused over this.
    """
    if not key or not getattr(user, "pk", None):
        return
    try:
        from django.core.cache import cache

        marker = "toto:session-seen:" + hashlib.sha256(key.encode()).hexdigest()[:32]
        if not cache.add(marker, 1, SEEN_EVERY):
            return
        UserSession, _ = _models()
        fields = {"last_seen_at": _now()}
        if request is not None:
            # A socket's key comes without an HTTP request: its address
            # stays the one it signed in from.
            fields["ip"] = _address(request)
        updated = UserSession.objects.filter(session_key=key, user=user).update(**fields)
        if not updated:
            _record(user, key, request, kind or UserSession.KIND_BROWSER)
    except Exception as exc:  # noqa: BLE001 - last seen is a nicety
        log.warning("sessions: last seen not written (%s)", type(exc).__name__)


def _fingerprint(user_agent: str, address) -> str:
    return hashlib.sha256(f"{user_agent}\n{address or ''}".encode()).hexdigest()


def note_sign_in(user, request) -> bool:
    """Remember this sign-in's (user agent, address); was it a new pair?

    New means not seen for this member in ``KNOWN_FOR_DAYS`` days — except
    on the first sign-in of an account with nothing known at all. Lapsed
    pairs are dropped only after this one is written, so a member back after
    a long absence still has a row and their next sign-in is news again.
    """
    _, KnownSignIn = _models()
    now = _now()
    cutoff = now - timedelta(days=KNOWN_FOR_DAYS)
    fingerprint = _fingerprint(_user_agent(request), _address(request))
    known = KnownSignIn.objects.filter(user=user)
    first_ever = not known.exists()
    seen = known.filter(fingerprint=fingerprint, last_seen_at__gte=cutoff).update(
        last_seen_at=now)
    if not seen:
        KnownSignIn.objects.update_or_create(user=user, fingerprint=fingerprint,
                                             defaults={"last_seen_at": now})
    known.filter(last_seen_at__lt=cutoff).delete()
    return not seen and not first_ever


# ── Signals ────────────────────────────────────────────────────────────────


def on_logged_in(sender, request, user, **kwargs):
    """``user_logged_in``: write the row, and tell the member of a new place.

    Its own savepoint and never a raise: a sign-in does not fail over this.
    """
    session = getattr(request, "session", None) if request is not None else None
    if session is None or not getattr(user, "pk", None):
        return
    from django.db import transaction

    UserSession, _ = _models()
    new_place = False
    try:
        if not session.session_key:
            session.save()
        with transaction.atomic():
            _record(user, session.session_key, request,
                    getattr(request, KIND_ATTR, None) or UserSession.KIND_BROWSER)
            new_place = note_sign_in(user, request)
    except Exception as exc:  # noqa: BLE001 - never in the way of a sign-in
        log.warning("sessions: sign-in of account %s not recorded (%s)",
                    user.pk, type(exc).__name__)
        return
    if new_place:
        from toto.core.notices import send_notice

        send_notice(user, "new_sign_in", {
            "address": _address(request) or "",
            "user_agent": _user_agent(request),
            "kind": getattr(request, KIND_ATTR, None) or UserSession.KIND_BROWSER,
        })


def on_logged_out(sender, request, user, **kwargs):
    """``user_logged_out``: the session is flushed right after; so is its row."""
    session = getattr(request, "session", None) if request is not None else None
    key = getattr(session, "session_key", None)
    if not key:
        return
    try:
        _models()[0].objects.filter(session_key=key).delete()
    except Exception as exc:  # noqa: BLE001 - the list drops it once the session is gone
        log.warning("sessions: row not removed at sign-out (%s)", type(exc).__name__)


def connect() -> None:
    from django.contrib.auth.signals import user_logged_in, user_logged_out

    user_logged_in.connect(on_logged_in, weak=False,
                           dispatch_uid="toto.core.user_sessions.logged_in")
    user_logged_out.connect(on_logged_out, weak=False,
                            dispatch_uid="toto.core.user_sessions.logged_out")
