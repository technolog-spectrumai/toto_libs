"""Sign-in lockout: growing waits, then a pause, per name and address (2026-09-30).

Until now the only brake on guessing a password was a 3-second cooldown kept
in the browser's own session (``auth_cooldown``) — a client that dropped its
cookie reset it — and it did not reach ``/api/login/`` or ``/admin/login/`` at
all. Every door that checks a password calls
``django.contrib.auth.authenticate(request, ...)``: the sign-in form
(``core:login``, and ``sso:login`` in every auth mode, which share
``auth_views.password_login_view``), the desktop's ``/api/login/`` and Django's
admin login. So the lockout is enforced once, there:

* ``SigninLockoutBackend``, first in ``AUTHENTICATION_BACKENDS``
  (``toto.auth_config.authentication_backends`` puts it ahead of
  ModelBackend), authenticates nobody. While a sign-in is held it raises
  ``PermissionDenied``, and Django then stops before any backend has compared
  a password — so the right password is refused too, and it costs no hashing.
* ``on_login_failed`` (``user_login_failed``) counts a refused password
  against the pair (the username as typed, normalised, plus the client
  address) and against the address alone.
* ``on_logged_in`` (``user_logged_in``) clears that pair's count.

The rule, with the owner's numbers as defaults (0 turns a rule off):

| setting | default | |
|---|---|---|
| ``LOGIN_DELAY_AFTER`` | 5 | failures of one pair before every further try must wait |
| ``LOGIN_DELAY_MAX_SECONDS`` | 60 | the wait is 2^(failures - LOGIN_DELAY_AFTER) seconds, up to this |
| ``LOGIN_LOCK_AFTER`` | 10 | failures of one pair that pause it |
| ``LOGIN_LOCK_MINUTES`` | 15 | how long a pause lasts |
| ``LOGIN_ADDRESS_LOCK_AFTER`` | 50 | failures from one address, any names, that pause the address |
| ``LOGIN_FAILURE_WINDOW_MINUTES`` | 15 | a count is forgotten this long after its last failure |

A wait is answered at once with how long is left — never a sleep on the
server, which would hand a guesser a worker to hold.

**Never the account itself** (the owner's decision). Every count and every
pause names an address. A stranger who fails a member's password from
elsewhere pauses (member, stranger's address) and, past fifty, the
stranger's address; the member signing in from their own is not asked to
wait. The cost: everybody behind one address — an office's NAT, a Tor exit —
shares its count.

**Nothing about the account.** Counting is by the name as typed, whether or
not an account has it, and is decided before any backend looks the name up;
the refusal is one sentence for both. A pause says nothing about who exists.

**The address** is ``toto.core.client_ip`` (nginx's X-Real-IP, believed only
from a trusted proxy). IPv6 counts per /64: that is what one household or one
server is handed, and counting single addresses would give a guesser 2^64
fresh starts.

**No request, no lockout.** ``authenticate()`` without a request — a
management command, a test client's ``login()`` — has no address, so it is
neither counted nor refused. Every web door passes its request.

**Fail open**, like ``toto.core.ratelimit`` whose counters these are: a cache
that cannot answer counts nothing and holds nobody, and says so in the log.
On a deployed stack the cache is Redis, shared by every web process.

**Unlocking** is console only: ``manage.py unlock_signin --user NAME |
--address IP | --all`` (``deploy.py <config> unlock-signin``). The cache has no
portable way to list keys, and this needs none: every key sits under
generation tokens — one for everything, one per address, one per name — and
unlocking replaces the token, so the old keys are never read again and expire
on their own.

**On the audit chain** (``toto.audit.identity``): ``AUTH.LOCKED`` once, when a
pair or an address is paused, with the name and the address; a refused try is
``AUTH.LOGIN_FAILED`` with ``refused`` saying why, at most once a minute per
pair or address (a paused guesser costs no hashing, so every knock recorded
would be a way to fill the chain); ``AUTH.UNLOCKED`` from the console. Never a
password.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import math
import time
import unicodedata
import uuid
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.utils.translation import ngettext

from toto.core import ratelimit
from toto.core.client_ip import client_ip, parse_address

log = logging.getLogger(__name__)

BACKEND_PATH = "toto.core.signin_lockout.SigninLockoutBackend"

#: Where the backend leaves its refusal for the door and the chain to read.
REQUEST_ATTR = "signin_refusal"

DELAY = "delay"                    # this name from this address, for seconds
LOCKED = "locked"                  # this name from this address, for minutes
ADDRESS_LOCKED = "address_locked"  # every name from this address, for minutes

#: A paused guesser's knocks reach the chain at most this often per key.
NOTE_EVERY_SECONDS = 60

DEFAULTS = {
    "LOGIN_DELAY_AFTER": 5,
    "LOGIN_DELAY_MAX_SECONDS": 60,
    "LOGIN_LOCK_AFTER": 10,
    "LOGIN_LOCK_MINUTES": 15,
    "LOGIN_ADDRESS_LOCK_AFTER": 50,
    "LOGIN_FAILURE_WINDOW_MINUTES": 15,
}


def setting(name: str) -> int:
    try:
        return max(0, int(getattr(settings, name, DEFAULTS[name])))
    except (TypeError, ValueError):
        return DEFAULTS[name]


def enabled() -> bool:
    """Only a host that lists the backend counts: a count nothing enforces
    would be cache traffic for nothing."""
    return BACKEND_PATH in (getattr(settings, "AUTHENTICATION_BACKENDS", None) or ())


def _clock() -> float:
    return time.time()


@dataclass(frozen=True)
class Refusal:
    reason: str        # DELAY, LOCKED or ADDRESS_LOCKED
    retry_after: int   # whole seconds, at least 1
    record: bool = True  # the first knock this minute: goes on the chain

    @property
    def message(self) -> str:
        """One sentence whatever the name was — so it cannot tell anybody
        whether an account has it."""
        if self.reason == DELAY:
            return ngettext(
                "Too many failed sign-in attempts. Wait %(seconds)d second and try again.",
                "Too many failed sign-in attempts. Wait %(seconds)d seconds and try again.",
                self.retry_after) % {"seconds": self.retry_after}
        minutes = max(1, math.ceil(self.retry_after / 60))
        return ngettext(
            "Too many failed sign-in attempts. Signing in is paused for %(minutes)d minute.",
            "Too many failed sign-in attempts. Signing in is paused for %(minutes)d minutes.",
            minutes) % {"minutes": minutes}


def refusal_for(request) -> Refusal | None:
    """The refusal the backend left on ``request`` in this authenticate(), if any."""
    return getattr(request, REQUEST_ATTR, None) if request is not None else None


# --- names, addresses and keys ----------------------------------------------

def normalise_username(username) -> str:
    """The name as counted: NFKC, trimmed, case-folded — "Ada" and "ada "
    are one guesser's two spellings of one target."""
    return unicodedata.normalize("NFKC", str(username or "")).strip().casefold()


def _name_tag(username) -> str:
    # Hashed: a key must not carry a space, a control character or a
    # 10 000-character "username" into the cache.
    return hashlib.sha256(normalise_username(username).encode("utf-8")).hexdigest()[:32]


def address_bucket(address) -> str:
    """The address as counted: IPv4 as it is, IPv6 per /64; "" if none."""
    parsed = parse_address(address)
    if parsed is None:
        return ""
    if parsed.version == 6:
        return str(ipaddress.ip_network(f"{parsed}/64", strict=False))
    return str(parsed)


_GEN_ALL = "signin:gen"


def _gen_name(tag: str) -> str:
    return f"signin:gen:u:{tag}"


def _gen_address(bucket: str) -> str:
    return f"signin:gen:a:{bucket}"


@dataclass(frozen=True)
class _Keys:
    pair: str
    address: str

    @property
    def pair_lock(self) -> str:
        return f"{self.pair}:lock"

    @property
    def pair_wait(self) -> str:
        return f"{self.pair}:wait"

    @property
    def address_lock(self) -> str:
        return f"{self.address}:lock"


def _keys(username, bucket: str) -> _Keys | None:
    tag = _name_tag(username)
    names = [_GEN_ALL, _gen_address(bucket), _gen_name(tag)]
    try:
        found = cache.get_many(names)
    except Exception:  # noqa: BLE001 - fail open, like the limiter
        log.warning("sign-in lockout: cache unavailable, nobody is held")
        return None
    everything, per_address, per_name = (str(found.get(name) or "0") for name in names)
    return _Keys(pair=f"signin:{everything}.{per_address}.{per_name}:p:{tag}:{bucket}",
                 address=f"signin:{everything}.{per_address}:a:{bucket}")


def _where(request) -> str:
    return address_bucket(client_ip(request)) if request is not None else ""


# --- asking, counting, clearing ---------------------------------------------

def refusal(request, username, *, now: float | None = None) -> Refusal | None:
    """Why a password for ``username`` may not be tried from here now, or None."""
    bucket = _where(request)
    if not bucket:
        return None
    keys = _keys(username, bucket)
    if keys is None:
        return None
    now = _clock() if now is None else now
    for reason, key in ((ADDRESS_LOCKED, keys.address_lock), (LOCKED, keys.pair_lock),
                        (DELAY, keys.pair_wait)):
        until = ratelimit.held_until(key, now=now)
        if until:
            first = ratelimit.hold(f"{key}:noted", seconds=NOTE_EVERY_SECONDS, now=now)
            return Refusal(reason, max(1, math.ceil(until - now)), record=first)
    return None


def note_failure(request, username, *, now: float | None = None) -> None:
    """A password refused: count it for the pair and for the address, and
    start a wait or a pause when a count reaches its threshold."""
    bucket = _where(request)
    if not bucket:
        return
    keys = _keys(username, bucket)
    if keys is None:
        return
    now = _clock() if now is None else now
    window = setting("LOGIN_FAILURE_WINDOW_MINUTES") * 60
    lock_seconds = setting("LOGIN_LOCK_MINUTES") * 60
    if not window:
        return
    delay_after, lock_after = setting("LOGIN_DELAY_AFTER"), setting("LOGIN_LOCK_AFTER")
    address_after = setting("LOGIN_ADDRESS_LOCK_AFTER")

    failures = ratelimit.count(keys.pair, window=window)
    if failures is not None:
        if lock_after and lock_seconds and failures >= lock_after:
            if ratelimit.hold(keys.pair_lock, seconds=lock_seconds, now=now):
                _on_chain_locked(request, scope="account_address", username=username,
                                 address=bucket, failures=failures)
            # The pause is the whole sentence: when it ends the pair starts
            # again from nothing, five free tries and the waits after them.
            ratelimit.forget(keys.pair, keys.pair_wait)
        elif delay_after and failures >= delay_after:
            wait = min(setting("LOGIN_DELAY_MAX_SECONDS"),
                       2 ** min(failures - delay_after, 30))
            ratelimit.hold(keys.pair_wait, seconds=wait, replace=True, now=now)

    from_here = ratelimit.count(keys.address, window=window)
    if from_here is not None and address_after and lock_seconds and from_here >= address_after:
        if ratelimit.hold(keys.address_lock, seconds=lock_seconds, now=now):
            _on_chain_locked(request, scope="address", username="", address=bucket,
                             failures=from_here)
        ratelimit.forget(keys.address)


def note_success(request, user) -> None:
    """A sign-in from here clears the pair's count and wait — not a pause,
    which a password could not have got through anyway, and not the
    address's count, which is everybody's at that address."""
    bucket = _where(request)
    if not bucket:
        return
    keys = _keys(user.get_username(), bucket)
    if keys is not None:
        ratelimit.forget(keys.pair, keys.pair_wait)


def _on_chain_locked(request, **fields) -> None:
    from django.apps import apps

    if not apps.is_installed("toto.audit"):
        return
    from toto.audit.identity import on_signin_locked

    on_signin_locked(request=request, minutes=setting("LOGIN_LOCK_MINUTES"), **fields)


# --- the console's unlock ----------------------------------------------------

def _new_generation(name: str) -> None:
    token = uuid.uuid4().hex[:12]
    cache.set(name, token, timeout=None)
    # django-redis with IGNORE_EXCEPTIONS swallows a failed write: read it
    # back, so the console never says "unlocked" about something that is not.
    if cache.get(name) != token:
        raise RuntimeError("the cache did not keep the new token")


def unlock_name(username) -> None:
    """Every pair with this name, from every address."""
    _new_generation(_gen_name(_name_tag(username)))


def unlock_address(address) -> str:
    """The address's own pause and every pair tried from it; returns the
    address as counted (an IPv6 address's /64)."""
    bucket = address_bucket(address)
    if not bucket:
        raise ValueError(f"{address!r} is not an IP address")
    _new_generation(_gen_address(bucket))
    return bucket


def unlock_all() -> None:
    _new_generation(_GEN_ALL)


# --- the backend and the receivers -------------------------------------------

class SigninLockoutBackend:
    """Authenticates nobody; refuses every password while its sign-in is held.

    No ``get_user``, on purpose: no session ever names this backend, and a
    backend that has one is what ``Client.force_login`` (and anything else
    that looks for "the first backend that can load a user") would pick —
    signing the session in through a backend that loads nobody.
    """

    def authenticate(self, request, username=None, password=None, **credentials):
        if request is None or password is None:
            return None
        setattr(request, REQUEST_ATTR, None)
        if username is None:
            from django.contrib.auth import get_user_model

            username = credentials.get(get_user_model().USERNAME_FIELD)
        held = refusal(request, username)
        if held is None:
            return None
        setattr(request, REQUEST_ATTR, held)
        raise PermissionDenied(held.message)


def on_login_failed(sender, credentials, request=None, **kwargs):
    if request is None or not enabled():
        return
    if refusal_for(request) is not None:
        # Refused by the lockout itself: no password was compared, so it is
        # not one more guess.
        return
    credentials = credentials or {}
    if "password" not in credentials:
        return
    from django.contrib.auth import get_user_model

    username = credentials.get("username", credentials.get(get_user_model().USERNAME_FIELD))
    note_failure(request, username)


def on_logged_in(sender, request, user, **kwargs):
    if request is None or user is None or not enabled():
        return
    note_success(request, user)


def connect() -> None:
    from django.contrib.auth.signals import user_logged_in, user_login_failed

    user_login_failed.connect(on_login_failed, weak=False,
                              dispatch_uid="toto.core.signin_lockout.failed")
    user_logged_in.connect(on_logged_in, weak=False,
                           dispatch_uid="toto.core.signin_lockout.signed_in")
