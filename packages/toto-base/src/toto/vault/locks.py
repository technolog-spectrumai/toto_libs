"""Editing locks: one person edits a document at a time.

The preventive layer. Two things make it work and both are deliberate.

**The database decides, not this module.** ``FileLock.file`` is a
``OneToOneField``, so two holders is not a race this code has to win — it is a
row the database refuses to store. What is here is ``select_for_update`` to make
the common case orderly and a re-read on ``IntegrityError`` for the uncommon
one, exactly the arrangement ``toto.mint.services.append_event`` uses.

**Expiry is read, never swept.** A lock is live because its ``expires_at`` is in
the future, not because a job has not deleted it yet. There is no cron, nothing
to fall behind, and a host whose worker is down does not slowly lock its whole
vault. A stale row is simply not a lock, and the next person to want the file
takes it over.

Absence of a lock is not a refusal. A save arriving with no lock at all acquires
one, because otherwise shipping this would break every editor already open in
somebody's browser. Only *someone else's live* lock refuses.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

#: How long a lock survives without a heartbeat. Four missed beats of slack at
#: the 30 s heartbeat below — enough that a slow request never drops a lock, and
#: short enough that a closed laptop frees the file while the other person is
#: still looking at the screen.
LOCK_TTL = timedelta(minutes=2)

#: What the editor should be told to use. Exported so the interval lives beside
#: the TTL it has to stay under, rather than as a number buried in three
#: different JS files.
HEARTBEAT_SECONDS = 30


class Locked(Exception):
    """Somebody else is editing. Carries the holder so callers can say who."""

    def __init__(self, lock):
        self.lock = lock
        super().__init__(f"Held by {lock.holder} until {lock.expires_at:%H:%M}")


def holder_of(vault_file):
    """The live lock on this file, or None. Expired rows are not locks."""
    from .models import FileLock

    lock = FileLock.objects.select_related("holder").filter(
        file=vault_file, expires_at__gt=timezone.now()).first()
    return lock


def acquire(vault_file, user, *, ttl: timedelta = LOCK_TTL):
    """Take or refresh the lock. Raises :class:`Locked` if someone else holds it.

    Re-acquiring your own lock is a refresh, not an error — a user reopening
    their own document, or having it open in two tabs, must never be locked out
    of it by themselves. Layer two catches the two-tab case.
    """
    from .models import FileLock

    if not getattr(user, "is_authenticated", False):
        raise Locked(_AnonymousHolder())

    now = timezone.now()
    with transaction.atomic():
        existing = (FileLock.objects.select_for_update()
                    .select_related("holder").filter(file=vault_file).first())
        if existing is not None:
            if existing.expires_at > now and existing.holder_id != user.pk:
                raise Locked(existing)
            # Ours, or dead. Either way it becomes ours now.
            existing.holder = user
            existing.expires_at = now + ttl
            if existing.holder_id != user.pk:
                existing.acquired_at = now
            existing.save(update_fields=["holder", "expires_at"])
            return existing

        try:
            with transaction.atomic():
                return FileLock.objects.create(
                    file=vault_file, holder=user, expires_at=now + ttl)
        except IntegrityError:
            # Someone inserted between the SELECT and the INSERT. The OneToOne
            # constraint is what stopped a second holder existing; re-read and
            # let the ordinary rules decide.
            current = (FileLock.objects.select_related("holder")
                       .filter(file=vault_file).first())
            if current is not None and current.expires_at > now \
                    and current.holder_id != user.pk:
                raise Locked(current) from None
            return acquire(vault_file, user, ttl=ttl)


def heartbeat(vault_file, user, *, ttl: timedelta = LOCK_TTL) -> bool:
    """Push the expiry out. False if the caller is no longer the holder.

    False is not an error — it is the editor being told, on its next beat, that
    the document moved on without it. That is how a client finds out it went to
    sleep and lost the lock.
    """
    from .models import FileLock

    now = timezone.now()
    updated = FileLock.objects.filter(
        file=vault_file, holder=user, expires_at__gt=now
    ).update(expires_at=now + ttl)
    return bool(updated)


def release(vault_file, user) -> bool:
    """Give the lock up. Only the holder can; a stale caller is a no-op.

    Called eagerly by the editor on ``pagehide``. Expiry is the backstop, so
    losing this request costs a couple of minutes rather than correctness.
    """
    from .models import FileLock

    deleted, _ = FileLock.objects.filter(file=vault_file, holder=user).delete()
    return bool(deleted)


def may_write(vault_file, user) -> bool:
    """Is this user allowed to write right now, lock-wise?

    True when nobody holds it, when it is held by them, or when the holder's
    lock has expired. Only a live lock belonging to somebody else says no.
    """
    lock = holder_of(vault_file)
    return lock is None or lock.holder_id == getattr(user, "pk", None)


class _AnonymousHolder:
    """Stand-in so :class:`Locked` can be raised at an unauthenticated caller."""

    holder = "an anonymous session"
    expires_at = timezone.now()
