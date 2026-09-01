"""The email password, held in process memory and nowhere else.

This module exists for the third custody mode: **no stored secret at all**. The
other two keep the SMTP password on the server in some form — encrypted in the
database under the ambient env passphrase (the default), or encrypted in the
database with the passphrase typed per release (``JESS_MANUAL_RELEASE``). Here
the password is never written anywhere: a staff member types it once per login
session on ``/jess/unlock/``, it lives in this dict keyed by their session key,
and it is gone on logout, on expiry, or whenever the process restarts.

What that buys and what it costs, stated plainly because both are real:

* Nothing to steal at rest. A database dump, a disk image, a leaked ``.env`` —
  none of them contains the SMTP password (unless the operator chose the
  bootstrap below, which is exactly the documented trade).
* **Per process.** The dict is web-process memory. A second gunicorn worker, a
  Celery worker, a restarted container — none of them holds the credential
  until someone unlocks there. This is why the password-reset flow that relies
  on this module sends **inline in the web request** rather than queueing to
  Celery: the worker that would dequeue the job cannot see this dict.
* A restart silently flips the platform from the email flow to the
  patron-ticket flow (``sso_core.password_reset``). That is by design — the
  fallback is a feature, not an outage — but it is worth knowing when a
  "resets stopped emailing" report comes in.

The session key is only a *label* for revocation (drop on logout). The
credential is deliberately NOT stored in ``request.session`` itself: zenobia's
sessions are database-backed, so anything put there is persisted to Postgres —
the one place this password must never be. ``storage_pin.py``'s critique of
timed session unlocks is answered the same way: nothing here outlives the
process, and nothing is readable outside it.

**The bootstrap credential.** ``settings.JESS_EMAIL_PASSWORD`` (env, written by
the deploy config — the builder offers the field) is read once, lazily, into
the same dict under a reserved key with no expiry. It makes the email flow
work from boot with nobody logged in. The cost is the password sitting in
``.env`` in plaintext, the very thing ``jess/README.md`` argues against — the
builder's field hint says so out loud, and leaving it blank keeps the pure
session-unlock custody.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from django.conf import settings

#: Reserved store key for the env-supplied bootstrap credential. Not a valid
#: Django session key (those are alphanumeric), so it can never collide.
_BOOTSTRAP_KEY = "__bootstrap__"


@dataclass
class _Entry:
    password: str
    expires_at: float | None  # epoch seconds; None = lives until lock/restart
    stored_at: float


_store: dict[str, _Entry] = {}
_lock = threading.Lock()
_bootstrapped = False


def _ttl_seconds() -> int:
    """Upper bound on how long an unlocked credential may live, logout aside.

    0 means NO cap — the credential lives until logout or restart. The same
    zero-disables convention as the auth cooldowns; "expire immediately" is
    not a state anyone means to configure.
    """
    return max(0, int(getattr(settings, "JESS_CREDENTIAL_TTL_SECONDS", 12 * 3600)))


def _purge_locked(now: float) -> None:
    for key, entry in list(_store.items()):
        if entry.expires_at is not None and entry.expires_at <= now:
            del _store[key]


def _bootstrap_locked() -> None:
    """Load the env-supplied password, once. Idempotent, called under _lock."""
    global _bootstrapped
    if _bootstrapped:
        return
    _bootstrapped = True
    password = (getattr(settings, "JESS_EMAIL_PASSWORD", "") or "").strip()
    if password:
        _store[_BOOTSTRAP_KEY] = _Entry(password=password, expires_at=None,
                                        stored_at=time.time())


def unlock(session_key: str, password: str) -> None:
    """Hold ``password`` for this login session, in this process only."""
    if not session_key or not password:
        return
    now = time.time()
    ttl = _ttl_seconds()
    with _lock:
        _bootstrap_locked()
        _purge_locked(now)
        _store[session_key] = _Entry(
            password=password,
            expires_at=(now + ttl) if ttl else None,
            stored_at=now,
        )


def lock(session_key: str | None) -> None:
    """Drop the credential this session unlocked. Safe to call with None."""
    if not session_key:
        return
    with _lock:
        _store.pop(session_key, None)


def lock_all() -> None:
    """Drop every SESSION credential and reset the store. Mostly for tests.

    Not a panic button for the bootstrap: that credential is configuration,
    not session state, so the next read loads it from the environment again —
    exactly as a restart would. Revoking a compromised bootstrap password
    means rotating it at the provider and redeploying, and nothing in-process
    can shortcut that.
    """
    global _bootstrapped
    with _lock:
        _store.clear()
        _bootstrapped = False


def credential() -> str | None:
    """Any live credential this process holds, or None.

    A session-typed password wins over the bootstrap: it is the fresher fact,
    and typing one is how an operator rotates the account password without a
    redeploy.
    """
    now = time.time()
    with _lock:
        _bootstrap_locked()
        _purge_locked(now)
        sessions = [e for k, e in _store.items() if k != _BOOTSTRAP_KEY]
        if sessions:
            return max(sessions, key=lambda e: e.stored_at).password
        entry = _store.get(_BOOTSTRAP_KEY)
        return entry.password if entry else None


def available() -> bool:
    return credential() is not None


def held_for(session_key: str | None) -> bool:
    """Did THIS session unlock a still-live credential? For the unlock page."""
    if not session_key:
        return False
    now = time.time()
    with _lock:
        _purge_locked(now)
        return session_key in _store


def bootstrap_present() -> bool:
    """Is the env bootstrap the thing keeping the email flow alive?"""
    with _lock:
        _bootstrap_locked()
        return _BOOTSTRAP_KEY in _store


def on_user_logged_out(sender, request, **kwargs):
    """``user_logged_out`` receiver: a session that ends takes its credential.

    Django sends the signal before flushing the session, so the key is still
    readable here. Wired in ``JessConfig.ready``.

    A session key that ROTATES without a logout — a fresh login over a live
    session cycles it — orphans that key's entry: unreachable by any page,
    gone at TTL. Harmless (the newest credential still wins) and not worth a
    login receiver that would drop a colleague's unlock on shared fixtures.
    """
    session = getattr(request, "session", None)
    lock(getattr(session, "session_key", None))
