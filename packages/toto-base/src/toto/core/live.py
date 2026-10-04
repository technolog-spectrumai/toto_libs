"""The change signal (2026-10-04): how the server says THAT something changed.

An open page asks one door — ``notify:api_wait`` — "anything new since my
cursor?", and the door holds the request until there is: long polling. (The
WebSocket this module fed for a day, ``ws/live/``, is gone, and the channel
layer with it.) This module is what that door waits on, and how the rest of
the library says a change:

    live.publish(live.user_key(user.pk))      # this member's bell has news
    live.publish("folder.7")                  # a file changed in folder 7

A KEY names what changed and nothing else — an account, a folder — never a
file's name, a sentence or who did it. What changed is asked for afterwards,
through a door that checks the reader (``notify:api_list``,
``vault:file_row``).

``publish(key)`` runs once the current transaction commits (at once outside
one) and never raises. It does three things:

1. **A new stamp for the key, in the cache.** A page's cursor is the stamps
   it has seen, and the door compares them — so a change is found by a page
   that was not waiting when it happened: between two polls, or while its
   tab was hidden.
2. **It wakes the requests waiting on that key in this process.**
3. **It says the key on a Redis channel** (``LIVE_REDIS_URL``, pub/sub), to
   which every web process keeps ONE subscriber connection — not one per
   waiting request — and wakes its own waiters when it hears it. A worker's
   change reaches the page of a member that way.

A request waits with ``Waiter`` and ``wait``: on the event loop, on an
``asyncio.Event`` — no polling while it is held, and nothing per waiting
request in Redis. Where no subscriber is up — no ``LIVE_REDIS_URL`` (the
tests, a developer's server), or Redis is away — a wait looks at the stamps
every ``POLL_SECONDS`` instead, which finds what another process published.

Nothing depends on a wake to be correct: a wake that is lost costs the time
to the next poll, never the change. No ``channels``, and no import of
``redis`` until a URL is set.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import threading
import time
from functools import partial

log = logging.getLogger("toto.core.live")

#: The Redis pub/sub channel every web process listens on.
CHANNEL = "toto.live"
#: A key's stamp in the cache, and how long it is kept. A stamp that expired
#: reads as no stamp: one page looks once more and finds nothing new.
STAMP_PREFIX = "toto.live."
STAMP_SECONDS = 24 * 3600
#: How often a wait looks at the stamps while no subscriber is up.
POLL_SECONDS = 1.0

_token = secrets.token_hex(4)
_lock = threading.Lock()
_waiters: dict = {}         # key -> set of Waiter
_listeners: dict = {}       # event loop -> _Listener
_publisher = None           # ((url, pid), client)
_warned = False


def user_key(user_pk) -> str:
    """One account: its bell."""
    return f"user.{int(user_pk)}"


def redis_url() -> str:
    """The Redis the wake-ups travel through, or "" for none."""
    from django.conf import settings

    return str(getattr(settings, "LIVE_REDIS_URL", "") or "")


def _origin() -> str:
    """This process, as its own messages are marked on the channel."""
    return f"{os.getpid():x}.{_token}"


# ---------------------------------------------------------------------------
# Saying it
# ---------------------------------------------------------------------------

def publish(key: str) -> None:
    """Say that ``key`` changed, once the current transaction commits. Never
    raises."""
    try:
        from django.db import transaction

        transaction.on_commit(partial(touch, str(key)))
    except Exception as exc:  # noqa: BLE001 - never in the way of the change
        _complain(exc)


def touch(key: str) -> None:
    """Say NOW that ``key`` changed: the stamp, this process's waiters, the
    other processes. Never raises."""
    try:
        from django.core.cache import cache

        stamp = f"{time.time_ns() // 1000:x}{secrets.randbelow(256):02x}"
        cache.set(STAMP_PREFIX + key, stamp, STAMP_SECONDS)
    except Exception as exc:  # noqa: BLE001 - the stamp is lost, the change is not
        _complain(exc)
    _wake(key)
    try:
        _tell_others(key)
    except Exception as exc:  # noqa: BLE001 - found at the next poll instead
        _complain(exc)


def stamps(keys) -> dict:
    """``{key: stamp}`` for ``keys``; "" for a key nobody has published (or
    whose stamp expired, or with the cache away)."""
    keys = list(keys)
    if not keys:
        return {}
    try:
        from django.core.cache import cache

        found = cache.get_many([STAMP_PREFIX + key for key in keys]) or {}
    except Exception as exc:  # noqa: BLE001 - no cache: nothing is new
        _complain(exc)
        found = {}
    return {key: str(found.get(STAMP_PREFIX + key) or "") for key in keys}


def _tell_others(key: str) -> None:
    url = redis_url()
    if not url:
        return
    global _publisher
    mine = (url, os.getpid())
    if _publisher is None or _publisher[0] != mine:
        import redis

        _publisher = (mine, redis.Redis.from_url(url, socket_connect_timeout=1,
                                                 socket_timeout=2))
    _publisher[1].publish(CHANNEL, f"{_origin()} {key}")


# ---------------------------------------------------------------------------
# Waiting
# ---------------------------------------------------------------------------

class Waiter:
    """One held request's ear. Made on the event loop that will wait;
    ``listen`` and ``close`` may be called from any thread."""

    __slots__ = ("loop", "event", "keys")

    def __init__(self, loop=None):
        self.loop = loop or asyncio.get_running_loop()
        self.event = asyncio.Event()
        self.keys = ()

    def listen(self, keys) -> None:
        """Hear ``keys`` from now on. Call it BEFORE reading the state the
        wait is about, so nothing said in between is missed."""
        keys = tuple(dict.fromkeys(str(key) for key in keys))
        with _lock:
            self.keys = tuple(dict.fromkeys(self.keys + keys))
            for key in keys:
                _waiters.setdefault(key, set()).add(self)

    def wake(self) -> None:
        try:
            self.loop.call_soon_threadsafe(self.event.set)
        except RuntimeError:        # the loop is gone, and its requests with it
            pass

    def reset(self) -> None:
        """Woken, looked, nothing to say: wait on."""
        self.event.clear()

    def close(self) -> None:
        with _lock:
            for key in self.keys:
                found = _waiters.get(key)
                if found is not None:
                    found.discard(self)
                    if not found:
                        del _waiters[key]


def waiting() -> int:
    """How many keys have a waiter in this process (for tests and metrics)."""
    with _lock:
        return len(_waiters)


def _wake(key: str) -> None:
    with _lock:
        found = list(_waiters.get(key, ()))
    for waiter in found:
        waiter.wake()


def _wake_all() -> None:
    with _lock:
        found = {waiter for group in _waiters.values() for waiter in group}
    for waiter in found:
        waiter.wake()


async def wait(waiter: Waiter, seconds: float, *, seen=None) -> bool:
    """Until ``waiter`` is woken — True — or ``seconds`` pass — False.

    ``seen`` is ``stamps(waiter.keys)`` as the caller read them after
    ``listen``: while no subscriber is up, the stamps are looked at every
    ``POLL_SECONDS`` and a difference counts as a wake.
    """
    loop = asyncio.get_running_loop()
    listener = _ensure_listener(loop)
    deadline = loop.time() + max(0.0, float(seconds))
    while True:
        if waiter.event.is_set():
            return True
        left = deadline - loop.time()
        if left <= 0:
            return False
        up = listener is not None and listener.up
        try:
            await asyncio.wait_for(waiter.event.wait(),
                                   left if up else min(left, POLL_SECONDS))
            return True
        except asyncio.TimeoutError:
            pass
        if not up and seen is not None and waiter.keys:
            from asgiref.sync import sync_to_async

            if await sync_to_async(stamps, thread_sensitive=True)(waiter.keys) != seen:
                return True


# ---------------------------------------------------------------------------
# The one subscriber of a process
# ---------------------------------------------------------------------------

class _Listener:
    __slots__ = ("task", "up")

    def __init__(self):
        self.task = None
        self.up = False


def listening() -> bool:
    """Is a subscriber of this process connected right now?"""
    return any(state.up for state in list(_listeners.values()))


def _ensure_listener(loop):
    url = redis_url()
    if not url:
        return None
    state = _listeners.get(loop)
    if state is None or state.task is None or state.task.done():
        state = _Listener()
        state.task = loop.create_task(_listen(url, state))
        _listeners[loop] = state
    return state


async def _listen(url: str, state: _Listener) -> None:
    """Subscribe, wake this process's waiters for every key heard, and come
    back after a lost connection, waiting longer each time."""
    import redis.asyncio as aioredis

    mine = _origin()
    delay = 1.0
    while True:
        client = None
        try:
            client = aioredis.Redis.from_url(url, socket_connect_timeout=2,
                                             socket_keepalive=True,
                                             health_check_interval=15)
            pubsub = client.pubsub(ignore_subscribe_messages=True)
            await pubsub.subscribe(CHANNEL)
            state.up = True
            delay = 1.0
            # Whatever was said while nobody here listened: everyone looks again.
            _wake_all()
            while True:
                message = await pubsub.get_message(timeout=20.0)
                if not message:
                    continue
                data = message.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8", "replace")
                origin, _, key = str(data or "").partition(" ")
                # Its own messages woke its own waiters already (touch).
                if key and origin != mine:
                    _wake(key)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - back to looking at the stamps
            _complain(exc)
        finally:
            state.up = False
            if client is not None:
                try:
                    await client.aclose()
                except Exception:  # noqa: BLE001 - it is being dropped anyway
                    pass
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30.0)


def _complain(exc) -> None:
    """Once loudly, then quietly: a Redis that is down fails every publish,
    and a warning per file would bury the log."""
    global _warned
    level = logging.DEBUG if _warned else logging.WARNING
    _warned = True
    log.log(level, "live: a change was not signalled (%s)", type(exc).__name__)
