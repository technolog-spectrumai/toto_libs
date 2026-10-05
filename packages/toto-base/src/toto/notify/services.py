"""Writing, listing and pruning notifications (2026-10-04).

``send`` is the one way a notification is made:

    send(user, "vault.uploaded", actor=ada, collapse="uploaded:7",
         link="/vault/public/?bucket=work", title="plan.pdf", bucket="Work",
         bucket_id=7)

* **The row, and nothing else.** The row is written in the caller's
  transaction. No page is told: nothing is published, no cache key is
  touched and no request waits for it (2026-10-06). The member's bell finds
  the row when it next asks ``notify:api_list`` — at a page load, or when
  its tab is looked at again.
* **Never in the way.** A notification is never worth failing the change it
  reports: anything that goes wrong is logged and answered ``None``, inside
  a savepoint so the caller's transaction stays usable.
* **Bursts fold.** ``collapse`` names what a burst shares; a second send with
  the same key while the first is still unread and younger than
  ``COLLAPSE_WINDOW`` raises that row's count instead of adding a row
  ("12 files were uploaded to Work").
* **Not to the one who did it**, and not to an account that cannot sign in.

``listing`` is what the bell draws: the latest rows and the unread count,
less the rows about a bucket that is hidden from the reader now
(pessimistic: a clearance taken away takes the notifications about that
bucket with it — they are deleted, not just skipped).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from . import kinds
from .models import Notification

log = logging.getLogger("toto.notify")

#: How many rows the bell shows.
LATEST = 20
#: A read notification is kept this long after it was read.
KEEP_READ_DAYS = 30
#: How long a burst keeps folding into its first row.
COLLAPSE_WINDOW = timedelta(minutes=10)
#: Rows deleted per query by the prune.
PRUNE_BATCH = 1000


def send(recipient, kind: str, *, actor=None, link: str = "", collapse: str = "",
         **params):
    """Tell ``recipient`` that ``kind`` happened; the row, or ``None`` when
    nothing was written. Never raises. See the module docstring."""
    try:
        if kinds.get(kind) is None:
            log.error("notify: unknown kind %r", kind)
            return None
        if recipient is None or not getattr(recipient, "pk", None):
            return None
        if not getattr(recipient, "is_active", True):
            return None
        actor = actor if getattr(actor, "pk", None) else None
        if actor is not None and actor.pk == recipient.pk:
            return None
        with transaction.atomic():
            row = _write(recipient, kind, actor, link, collapse, params)
        return row
    except Exception as exc:  # noqa: BLE001 - see the module docstring
        log.warning("notify: %s to account %s not written (%s)", kind,
                    getattr(recipient, "pk", None), type(exc).__name__)
        return None


def _write(recipient, kind, actor, link, collapse, params):
    now = timezone.now()
    key = (collapse or "")[:120]
    mine = Notification.objects.filter(recipient=recipient, kind=kind)
    if collapse:
        row = (mine.select_for_update()
               .filter(collapse_key=key, read_at__isnull=True,
                       created__gte=now - COLLAPSE_WINDOW)
               .order_by("-created", "-pk").first())
        if row is not None:
            values = dict(row.params or {})
            values["count"] = kinds.count_of(values) + 1
            row.params = values
            row.created = now
            # A burst by several people names none of them.
            if row.actor_id != (actor.pk if actor is not None else None):
                row.actor = None
            row.save(update_fields=["params", "created", "actor"])
            return row
    return Notification.objects.create(
        recipient=recipient, actor=actor, kind=kind, params=_plain(params),
        link=(link or "")[:300], collapse_key=key, created=now)


def _plain(params: dict) -> dict:
    """Text and numbers only: what a sentence can hold."""
    plain = {}
    for name, value in params.items():
        if value is None or isinstance(value, (bool, int, float)):
            plain[name] = value
        else:
            plain[name] = str(value)[:300]
    return plain


# ---------------------------------------------------------------------------
# What the bell reads
# ---------------------------------------------------------------------------

def unread_count(user) -> int:
    """How many of the member's notifications are unread — of the kinds this
    build tells (``kinds.KINDS``): a row of a kind that left is counted by
    no bell."""
    if not getattr(user, "is_authenticated", False):
        return 0
    return Notification.objects.filter(recipient=user, read_at__isnull=True,
                                       kind__in=list(kinds.KINDS)).count()


def _hidden_bucket_ids(user, bucket_ids) -> set:
    """Which of these buckets ``user`` may not be told about now: gone, or
    kept to clearances they do not hold. The vault's own rule
    (``socialhub.clearance_access``), no owner bypass."""
    from django.apps import apps

    if not bucket_ids or not apps.is_installed("toto.vault"):
        return set()
    from django.db.models import OuterRef

    from toto.socialhub.clearance_access import group_gate
    from toto.vault.models import Bucket

    readable = group_gate(user, Bucket.objects.filter(pk__in=bucket_ids),
                          groups=Bucket.objects.filter(pk=OuterRef("pk")))
    return set(bucket_ids) - set(readable.values_list("pk", flat=True))


def listing(user, *, limit: int = LATEST):
    """``(rows, unread)`` for the bell: the latest ``limit`` rows the reader
    may still be told, newest first, and how many of all theirs are unread.

    A row about a bucket hidden from the reader now is deleted on the way;
    one whose kind this build does not know is neither listed nor counted
    (and not deleted)."""
    from django.apps import apps

    related = "actor__community_profile" if apps.is_installed("toto.people") else "actor"
    rows = list(Notification.objects.filter(recipient=user, kind__in=list(kinds.KINDS))
                .select_related(related)[:limit])
    scoped = {}
    for row in rows:
        kind = kinds.get(row.kind)
        if kind is not None and kind.bucket_scoped:
            try:
                scoped[row.pk] = int((row.params or {}).get("bucket_id"))
            except (TypeError, ValueError):
                scoped[row.pk] = None
    hidden = _hidden_bucket_ids(user, {pk for pk in scoped.values() if pk})
    gone = [pk for pk, bucket_id in scoped.items() if bucket_id is None or bucket_id in hidden]
    if gone:
        Notification.objects.filter(recipient=user, pk__in=gone).delete()
        rows = [row for row in rows if row.pk not in set(gone)]
    return rows, unread_count(user)


def _name_of(user) -> str:
    if user is None:
        return ""
    profile = getattr(user, "community_profile", None)
    return (getattr(profile, "display_name", "") or user.get_username() or "").strip()


def payload(row) -> dict:
    """One row as the bell's door answers it, in the active language and
    time zone."""
    from django.utils import formats

    kind = kinds.get(row.kind)
    return {
        "id": row.pk,
        "kind": row.kind,
        "icon": kind.icon if kind is not None else "",
        "text": kinds.text_of(row.kind, row.params),
        "actor": _name_of(row.actor) if row.actor_id else "",
        "link": row.link,
        "created": row.created.isoformat(),
        "when": formats.date_format(timezone.localtime(row.created), "SHORT_DATETIME_FORMAT"),
        "read": row.read_at is not None,
    }


def mark_read(user, pk) -> bool:
    """Mark one of ``user``'s rows read; False when it is not theirs (or
    not there — the same answer)."""
    row = Notification.objects.filter(recipient=user, pk=pk).first()
    if row is None:
        return False
    if row.read_at is None:
        row.read_at = timezone.now()
        row.save(update_fields=["read_at"])
    return True


def mark_all_read(user) -> int:
    return Notification.objects.filter(recipient=user, read_at__isnull=True).update(
        read_at=timezone.now())


# ---------------------------------------------------------------------------
# The nightly prune
# ---------------------------------------------------------------------------

def prune(*, now=None, days: int = KEEP_READ_DAYS) -> int:
    """Delete the rows read more than ``days`` ago; how many went. An unread
    row waits, however old. Idempotent."""
    cutoff = (now or timezone.now()) - timedelta(days=days)
    deleted = 0
    while True:
        pks = list(Notification.objects.filter(read_at__isnull=False, read_at__lt=cutoff)
                   .values_list("pk", flat=True)[:PRUNE_BATCH])
        if not pks:
            return deleted
        Notification.objects.filter(pk__in=pks).delete()
        deleted += len(pks)
