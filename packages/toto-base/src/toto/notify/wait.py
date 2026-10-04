"""The long poll's working parts (2026-10-04): the cursor, the look, the cap.

The door is ``views.api_wait``; what it says and to whom is decided here.

**The cursor** is what a page has seen, handed back at its next poll: a
digest of the member's notifications and, for each folder the page watches,
that folder's stamp (``toto.core.live``). It is signed, tied to the account
and good for a day, so it is opaque to the page and nobody else's is taken;
a cursor that cannot be read counts as none. No cursor is "tell me where I
stand": the door answers at once, and a watched folder the cursor does not
know yet is reported once — which also brings a folder opened long after
the page was drawn up to date.

**The look** is taken afresh at every poll and after every wake:

* the notification digest comes from the DATABASE (``services.digest``), so
  the bell is right even with the cache away;
* the folders are put to the vault's own access rule first
  (``toto.vault.live.watched``); a folder the reader may not watch, or that
  is not there, has no key listened to, no stamp read and no place in the
  cursor — asking for it is the same as not asking;
* for a folder that changed, the answer carries the ids of the files the
  list shows this reader there (``toto.vault.live.rows``): ids and version
  tags, never a name.

**The cap.** ``MAX_WAITS`` held requests per account in one web process: a
fourth sends the oldest home empty-handed, with ``retry`` — how long its
page is to stay away — so tabs beyond the cap take turns instead of hammering.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from django.core import signing

from toto.core import live

from . import services

SALT = "toto.notify.wait"
#: How long a request is held. nginx cuts a silent upstream at 60 s
#: (``proxy_read_timeout``); a host sets ``NOTIFY_WAIT_SECONDS``.
HOLD_SECONDS = 25
#: The longest hold a setting may ask for.
HOLD_LIMIT = 50
#: Held requests per account, per web process (``NOTIFY_MAX_WAITS``).
MAX_WAITS = 3
#: Folders one poll may name.
MAX_FOLDERS = 50
#: A cursor longer than this, or older than a day, is no cursor.
MAX_CURSOR = 8192
CURSOR_SECONDS = 24 * 3600
#: What a page sent home by the cap, or served by a server that cannot hold
#: a request, is told to wait before it asks again.
RETRY_SECONDS = 25


def hold_seconds() -> float:
    from django.conf import settings

    try:
        seconds = float(getattr(settings, "NOTIFY_WAIT_SECONDS", HOLD_SECONDS))
    except (TypeError, ValueError):
        seconds = HOLD_SECONDS
    return min(max(seconds, 0.0), HOLD_LIMIT)


def max_waits() -> int:
    from django.conf import settings

    try:
        return max(1, int(getattr(settings, "NOTIFY_MAX_WAITS", MAX_WAITS)))
    except (TypeError, ValueError):
        return MAX_WAITS


# ---------------------------------------------------------------------------
# What a poll names, and the cursor
# ---------------------------------------------------------------------------

def folder_ids(raw) -> list:
    """The folder ids of ``?folders=1,2,3``: plain positive numbers, each
    once, at most ``MAX_FOLDERS``. Anything else in the list is skipped."""
    found = []
    for part in str(raw or "")[:MAX_FOLDERS * 24].split(","):
        part = part.strip()
        if not (part.isascii() and part.isdigit()) or len(part) > 18:
            continue
        pk = int(part)
        if pk > 0 and pk not in found:
            found.append(pk)
        if len(found) >= MAX_FOLDERS:
            break
    return found


def read_cursor(user, raw):
    """The state a cursor of THIS account holds, or ``None``."""
    raw = str(raw or "")
    if not raw or len(raw) > MAX_CURSOR:
        return None
    try:
        state = signing.loads(raw, salt=SALT, max_age=CURSOR_SECONDS)
    except signing.BadSignature:
        return None
    if not isinstance(state, dict) or state.get("u") != user.pk:
        return None
    if not isinstance(state.get("f"), dict):
        state["f"] = {}
    return state


def write_cursor(user, state: dict) -> str:
    return signing.dumps({"u": user.pk, "n": state["n"], "f": state["f"]}, salt=SALT,
                         compress=True)


# ---------------------------------------------------------------------------
# The look
# ---------------------------------------------------------------------------

@dataclass
class Look:
    watched: list                       # the folder ids the reader may watch
    state: dict                         # {"n": digest, "f": {id: stamp}}
    seen: dict = field(default_factory=dict)    # the stamps of the keys listened to


def _vault():
    from django.apps import apps

    if not apps.is_installed("toto.vault"):
        return None
    from toto.vault import live as vault_live

    return vault_live


def watched(user, ids) -> list:
    """The folders among ``ids`` this account may watch now; none on a host
    without the vault, and none when the question cannot be decided."""
    vault_live = _vault()
    if vault_live is None or not ids:
        return []
    try:
        return vault_live.watched(user, ids)
    except Exception as exc:  # noqa: BLE001 - what cannot be decided is not watched
        services.log.warning("notify: folders could not be decided (%s)", type(exc).__name__)
        return []


def keys_for(user, folders) -> list:
    """The keys a poll of ``user`` about ``folders`` (already checked)
    listens to: their own bell, and those folders."""
    vault_live = _vault()
    keys = [live.user_key(user.pk)]
    if vault_live is not None:
        keys += [vault_live.folder_key(pk) for pk in folders]
    return keys


def look(user, folders) -> Look:
    """Where ``user`` stands now, about ``folders`` (already checked)."""
    vault_live = _vault()
    seen = live.stamps(keys_for(user, folders))
    marks = ({str(pk): seen[vault_live.folder_key(pk)] for pk in folders}
             if vault_live is not None else {})
    return Look(watched=list(folders), state={"n": services.digest(user), "f": marks},
                seen=seen)


def answer(user, cursor, found: Look, *, anyway: bool = False):
    """What the door says, or ``None`` when there is nothing new and the
    request is to be held. ``anyway`` answers regardless (the hold ended)."""
    vault_live = _vault()
    before = cursor or {}
    marks = before.get("f") or {}
    news = cursor is not None and before.get("n") != found.state["n"]
    changed = [pk for pk in found.watched if marks.get(str(pk)) != found.state["f"][str(pk)]]
    if cursor is not None and not news and not changed and not anyway:
        return None
    data = {"cursor": write_cursor(user, found.state), "notifications": bool(news),
            "folders": changed}
    if changed and vault_live is not None:
        files = {}
        for pk in changed:
            rows = vault_live.rows(user, pk)
            if rows is not None:
                files[str(pk)] = rows
        data["files"] = files
    return data


def sent_home(user, found: Look, seconds: int = RETRY_SECONDS) -> dict:
    """The empty answer, with how long to stay away."""
    return {"cursor": write_cursor(user, found.state), "notifications": False,
            "folders": [], "retry": int(seconds)}


# ---------------------------------------------------------------------------
# The cap
# ---------------------------------------------------------------------------

class Slot:
    __slots__ = ("waiter", "bumped")

    def __init__(self, waiter):
        self.waiter = waiter
        self.bumped = False


_slots: dict = {}           # account pk -> its held requests, oldest first
_slots_lock = threading.Lock()


def claim(user_pk, waiter) -> Slot:
    """A place among the account's held requests in this process. Over the
    cap, the oldest are sent home (``bumped``) and woken to say so."""
    slot = Slot(waiter)
    cap = max_waits()
    with _slots_lock:
        mine = _slots.setdefault(user_pk, [])
        mine.append(slot)
        over = mine[:-cap]
        del mine[:-cap]
    for old in over:
        old.bumped = True
        old.waiter.wake()
    return slot


def release(user_pk, slot: Slot) -> None:
    with _slots_lock:
        mine = _slots.get(user_pk)
        if mine is None:
            return
        if slot in mine:
            mine.remove(slot)
        if not mine:
            del _slots[user_pk]


def held(user_pk=None) -> int:
    """How many requests are held in this process (for one account)."""
    with _slots_lock:
        if user_pk is not None:
            return len(_slots.get(user_pk, ()))
        return sum(len(mine) for mine in _slots.values())
