"""The timeline of a file and of a bucket (2026-10-12).

**What writes it.** One receiver of the vault's own ``file_changed`` signal,
so every writer is covered (a page, the JSON API, the admin, a worker, the
console) and no door had to learn it; the bucket's doors for what happens to
a bucket itself (made, changed, a deletion asked for, its clearances); the
copy log; the trash's purge.

**A file's history is four things** (the owner, 2026-10-12: "a table who
opened or edited or created or downloaded when, and that's it. For detecting
edits compare file hash"): **created**, **edited** (its content hash moved
from one value to another: the signal's own comparison), **opened** (served
for display) and **downloaded**, each with who and when. Somebody reading
the same file again within ten minutes is one row, not one a request.

**A bucket's history holds those and the rest**: renamed, moved, encrypted
or decrypted, made public or private, moved to the trash, restored, deleted,
purged; the bucket made or changed, files copied out of it or into it, its
clearances changed.

Never an address or a browser: those are the audit chain's, which the team
reads.

**Who reads it.** A file's: whoever ``access.may_read`` lets read the file.
A bucket's: its owner and superusers, and nobody the bucket is kept from by
its clearances.

Nothing here raises into the change it records.
"""

from __future__ import annotations

import logging
from functools import wraps

from django.utils.translation import gettext as _

log = logging.getLogger("toto.vault")

#: How many rows a page of a timeline holds.
PAGE = 50
#: How many rows a page of one file's history holds.
FILE_PAGE = 20

# The kinds. A file's:
CREATED, EDITED, OPENED, DOWNLOADED = "created", "edited", "opened", "downloaded"
RENAMED, MOVED, MOVED_OUT = "renamed", "moved", "moved_out"
ENCRYPTED, DECRYPTED, PUBLISHED, UNPUBLISHED = "encrypted", "decrypted", "published", "unpublished"
TRASHED, RESTORED, DELETED, PURGED = "trashed", "restored", "deleted", "purged"
# A bucket's own:
BUCKET_CREATED, BUCKET_CHANGED, BUCKET_DELETE_REQUESTED = (
    "bucket_created", "bucket_changed", "bucket_delete_requested")
CLEARANCES_CHANGED, COPIED_OUT, COPIED_IN = "clearances_changed", "copied_out", "copied_in"

#: A file's history: these four, and that is it.
HISTORY_KINDS = (CREATED, EDITED, OPENED, DOWNLOADED)
FILE_KINDS = HISTORY_KINDS + (RENAMED, MOVED, MOVED_OUT, ENCRYPTED, DECRYPTED, PUBLISHED,
                              UNPUBLISHED, TRASHED, RESTORED, DELETED, PURGED)
#: Somebody reading the same file again within this many minutes is one row.
READ_FOLD_MINUTES = 10
#: What a browser says it fetches a file FOR when it is shown, not saved.
SHOWN_AS = ("image", "video", "audio", "iframe", "embed", "object", "track")
BUCKET_KINDS = (BUCKET_CREATED, BUCKET_CHANGED, BUCKET_DELETE_REQUESTED, CLEARANCES_CHANGED,
                COPIED_OUT, COPIED_IN)

ICONS = {
    CREATED: "fa-solid fa-upload", EDITED: "fa-solid fa-pen-to-square",
    OPENED: "fa-solid fa-eye", DOWNLOADED: "fa-solid fa-download", RENAMED: "fa-solid fa-pencil",
    MOVED: "fa-solid fa-up-down", MOVED_OUT: "fa-solid fa-right-from-bracket",
    ENCRYPTED: "fa-solid fa-lock", DECRYPTED: "fa-solid fa-lock-open",
    PUBLISHED: "fa-solid fa-globe", UNPUBLISHED: "fa-solid fa-eye-slash",
    TRASHED: "fa-solid fa-trash", RESTORED: "fa-solid fa-trash-arrow-up",
    DELETED: "fa-solid fa-xmark", PURGED: "fa-solid fa-xmark",
    BUCKET_CREATED: "fa-solid fa-box", BUCKET_CHANGED: "fa-solid fa-sliders",
    BUCKET_DELETE_REQUESTED: "fa-solid fa-triangle-exclamation",
    CLEARANCES_CHANGED: "fa-solid fa-key", COPIED_OUT: "fa-solid fa-copy",
    COPIED_IN: "fa-solid fa-copy",
}


def quiet(function):
    """A timeline never breaks what it records."""
    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception:  # noqa: BLE001
            log.exception("vault: timeline %s", function.__name__)
            return None
    return guarded


def acting():
    """Whoever is at the keyboard for the request being served, or ``None``
    (a worker, the console)."""
    try:
        from toto.audit.context import current_context
    except ImportError:
        return None
    user = getattr(current_context(), "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return user


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

@quiet
def record(kind: str, *, bucket_id, file=None, title: str = "", actor=None, **details):
    """One row. ``file`` may be a row that is about to go or already gone:
    its number and title are kept either way."""
    from django.db import transaction

    from .models import VaultEvent, VaultFile

    if not bucket_id:
        return None
    number = getattr(file, "pk", None)
    # A foreign key only to a row that is there: a deleted one keeps its number.
    there = bool(number) and VaultFile.all_objects.filter(pk=number).exists()
    with transaction.atomic():      # a refused insert must not poison the caller's
        return VaultEvent.objects.create(
            bucket_id=bucket_id, file_id=number if there else None, file_number=number,
            file_title=(title or getattr(file, "title", "") or "")[:255], kind=kind,
            actor=actor if getattr(actor, "pk", None) else None,
            actor_name=(actor.get_username() if getattr(actor, "pk", None) else "")[:150],
            details={key: value for key, value in details.items() if value not in (None, "")})


def _folder(directory_id) -> str:
    if not directory_id:
        return ""
    from .models import VaultDirectory

    return (VaultDirectory.objects.filter(pk=directory_id).values_list("name", flat=True).first()
            or "")


def _bucket_name(bucket_id) -> str:
    from .models import Bucket

    return Bucket.objects.filter(pk=bucket_id).values_list("name", flat=True).first() or ""


@quiet
def on_file_changed(sender=None, file=None, kind="", was=None, **kwargs):
    """``file_changed`` into rows: one a thing that happened."""
    if file is None or not file.bucket_id or getattr(file, "origin", "") == "mirror":
        return          # a paired host's listing is not somebody's act here
    was = was or {}
    who = acting()
    if kind == "uploaded":
        record(CREATED, bucket_id=file.bucket_id, file=file, actor=who or file.owner,
               folder=_folder(file.directory_id))
    elif kind == "replaced":
        # The signal's comparison: the content hash moved from one value to another.
        record(EDITED, bucket_id=file.bucket_id, file=file, actor=who)
    elif kind == "trashed":
        record(TRASHED, bucket_id=file.bucket_id, file=file, actor=who or file.trashed_by,
               folder=_folder(was.get("directory_id")))
    elif kind == "restored":
        record(RESTORED, bucket_id=file.bucket_id, file=file, actor=who,
               folder=_folder(file.directory_id))
    elif kind == "deleted":
        record(DELETED, bucket_id=file.bucket_id, file=file, actor=who)
    elif kind == "moved":
        old_bucket = was.get("bucket_id")
        if old_bucket and old_bucket != file.bucket_id:
            record(MOVED_OUT, bucket_id=old_bucket, file=file, actor=who,
                   to_bucket=_bucket_name(file.bucket_id))
            record(MOVED, bucket_id=file.bucket_id, file=file, actor=who,
                   from_bucket=_bucket_name(old_bucket), to=_folder(file.directory_id))
        else:
            record(MOVED, bucket_id=file.bucket_id, file=file, actor=who,
                   **{"from": _folder(was.get("directory_id")), "to": _folder(file.directory_id)})
    elif kind == "changed":
        if "title" in was and was["title"] != file.title:
            record(RENAMED, bucket_id=file.bucket_id, file=file, actor=who,
                   **{"from": was["title"], "to": file.title})
        if "is_encrypted" in was and bool(was["is_encrypted"]) != bool(file.is_encrypted):
            record(ENCRYPTED if file.is_encrypted else DECRYPTED, bucket_id=file.bucket_id,
                   file=file, actor=who)
        if "is_public" in was and bool(was["is_public"]) != bool(file.is_public):
            record(PUBLISHED if file.is_public else UNPUBLISHED, bucket_id=file.bucket_id,
                   file=file, actor=who)


@quiet
def read(vault_file, *, inline: bool = False):
    """Somebody was handed the file's bytes: **opened** when they were served
    for display (``inline``, or a browser fetching them for a picture, a
    player or a frame), **downloaded** otherwise. By whoever is at the
    keyboard; a read with nobody there (a worker, another server) is not a
    row. Folded: the same person and file within ``READ_FOLD_MINUTES`` is the
    row that is there (a player asks for a film in many pieces)."""
    from datetime import timedelta

    from django.utils import timezone

    from .models import VaultEvent

    who = acting()
    if who is None or vault_file is None or not vault_file.bucket_id:
        return None
    shown = inline
    try:
        from toto.audit.context import current_context

        request = getattr(current_context(), "request", None)
        shown = shown or (request is not None
                          and request.headers.get("Sec-Fetch-Dest", "") in SHOWN_AS)
    except ImportError:
        pass
    kind = OPENED if shown else DOWNLOADED
    since = timezone.now() - timedelta(minutes=READ_FOLD_MINUTES)
    if VaultEvent.objects.filter(file_number=vault_file.pk, actor=who, kind=kind,
                                 at__gte=since).exists():
        return None
    return record(kind, bucket_id=vault_file.bucket_id, file=vault_file, actor=who)


@quiet
def on_copy_logged(sender=None, instance=None, created=False, raw=False, **kwargs):
    """A copy between buckets, as the copy log has it: a row in each."""
    if not created or raw or instance is None:
        return
    source, target = instance.from_bucket, instance.to_bucket
    if source is not None:
        record(COPIED_OUT, bucket_id=source.pk, actor=instance.performed_by,
               count=instance.file_count, bucket=getattr(target, "name", ""))
    if target is not None and (source is None or target.pk != source.pk):
        record(COPIED_IN, bucket_id=target.pk, actor=instance.performed_by,
               count=instance.file_count, bucket=getattr(source, "name", ""))


def bucket_event(action: str, bucket, actor, metadata=None) -> None:
    """What ``bucket_lifecycle.audit`` records, for the bucket's timeline."""
    kind = {"created": BUCKET_CREATED, "updated": BUCKET_CHANGED,
            "delete_requested": BUCKET_DELETE_REQUESTED}.get(action)
    if kind is None or bucket is None or not bucket.pk:
        return
    details = {}
    if kind == BUCKET_CHANGED:
        # Which of its settings, by name; the values are the audit chain's.
        changed = sorted((metadata or {}).get("changes") or {})
        if changed:
            details["fields"] = ", ".join(changed)
    record(kind, bucket_id=bucket.pk, actor=actor, **details)


def clearances_changed(bucket, actor, before, after) -> None:
    if list(before) == list(after):
        return
    record(CLEARANCES_CHANGED, bucket_id=bucket.pk, actor=actor,
           before=", ".join(before), after=", ".join(after))


def purged(vault_file, actor=None) -> None:
    """A file left the trash for good."""
    record(PURGED, bucket_id=getattr(vault_file, "bucket_id", None), file=vault_file, actor=actor)


@quiet
def forget_actor(sender=None, instance=None, **kwargs):
    """An erased account's name leaves the rows it acted in: they stay,
    nobody's (the foreign key is nulled by the delete itself)."""
    from .models import VaultEvent

    VaultEvent.objects.filter(actor=instance).update(actor_name="")


def connect() -> None:
    from django.contrib.auth import get_user_model
    from django.db.models.signals import post_save, pre_delete

    from .models import BucketCopyLog
    from .signals import file_changed

    file_changed.connect(on_file_changed, dispatch_uid="toto.vault.timeline.file_changed")
    post_save.connect(on_copy_logged, sender=BucketCopyLog,
                      dispatch_uid="toto.vault.timeline.copy_logged")
    pre_delete.connect(forget_actor, sender=get_user_model(),
                       dispatch_uid="toto.vault.timeline.forget_actor")


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def sentence(event) -> str:
    """What happened, in the reader's language. Names only."""
    d = event.details or {}
    kind = event.kind
    if kind == CREATED:
        return _("Created")
    if kind == EDITED:
        return _("Edited")
    if kind == OPENED:
        return _("Opened")
    if kind == DOWNLOADED:
        return _("Downloaded")
    if kind == RENAMED:
        return _("Renamed from %(from)s to %(to)s") % {"from": d.get("from", ""), "to": d.get("to", "")}
    if kind == MOVED:
        if d.get("from_bucket"):
            return _("Moved here from the bucket %(bucket)s") % {"bucket": d["from_bucket"]}
        return _("Moved from %(from)s to %(to)s") % {
            "from": d.get("from") or _("the bucket's top"), "to": d.get("to") or _("the bucket's top")}
    if kind == MOVED_OUT:
        return _("Moved to the bucket %(bucket)s") % {"bucket": d.get("to_bucket", "")}
    if kind == ENCRYPTED:
        return _("Encrypted")
    if kind == DECRYPTED:
        return _("Encryption removed")
    if kind == PUBLISHED:
        return _("Made public")
    if kind == UNPUBLISHED:
        return _("Made private")
    if kind == TRASHED:
        return _("Moved to the trash")
    if kind == RESTORED:
        return _("Restored from the trash")
    if kind == DELETED:
        return _("Deleted")
    if kind == PURGED:
        return _("Deleted for good from the trash")
    if kind == BUCKET_CREATED:
        return _("The bucket was made")
    if kind == BUCKET_CHANGED:
        return (_("The bucket's settings changed: %(fields)s") % d if d.get("fields")
                else _("The bucket's settings changed"))
    if kind == BUCKET_DELETE_REQUESTED:
        return _("The bucket's deletion was asked for")
    if kind == CLEARANCES_CHANGED:
        return _("Clearances changed from %(before)s to %(after)s") % {
            "before": d.get("before") or _("none"), "after": d.get("after") or _("none")}
    if kind == COPIED_OUT:
        return _("%(count)s file(s) copied to the bucket %(bucket)s") % {
            "count": d.get("count", ""), "bucket": d.get("bucket", "")}
    if kind == COPIED_IN:
        return _("%(count)s file(s) copied here from the bucket %(bucket)s") % {
            "count": d.get("count", ""), "bucket": d.get("bucket", "")}
    return kind


def row(event) -> dict:
    """An event as a page or a door hands it over: plain values."""
    from django.utils import timezone
    from django.utils.formats import date_format

    return {
        "id": event.pk, "kind": event.kind, "icon": ICONS.get(event.kind, "fa-solid fa-circle"),
        "what": sentence(event), "who": event.actor_name,
        "file": event.file_title, "file_number": event.file_number,
        "when": date_format(timezone.localtime(event.at), "Y-m-d H:i"),
        "at": event.at.isoformat(),
    }


def of_file(vault_file):
    """A file's history, newest first: who created, edited, opened or
    downloaded it, and when. By its number, so it is one history across the
    trash."""
    from .models import VaultEvent

    return VaultEvent.objects.filter(file_number=vault_file.pk, kind__in=HISTORY_KINDS)


def may_read_file_timeline(user, vault_file) -> bool:
    from . import access

    return bool(vault_file is not None and access.may_read(user, vault_file))


def may_read_bucket_timeline(user, bucket) -> bool:
    """Its owner and superusers: who the bucket's own page is for."""
    return bool(getattr(user, "is_authenticated", False)
                and (bucket.owner_id == user.pk or user.is_superuser))


def of_bucket(user, bucket):
    """A bucket's rows, newest first: nothing for somebody its clearances
    keep its files from, its owner included."""
    from toto.socialhub.clearance_access import group_hidden

    from .models import Bucket, VaultEvent

    if group_hidden(user, Bucket.objects.filter(pk=bucket.pk)):
        return VaultEvent.objects.none()
    return VaultEvent.objects.filter(bucket=bucket)


def kinds_named() -> list:
    """``[(kind, label)]`` for the bucket page's filter."""
    labels = {
        CREATED: _("Created"), EDITED: _("Edited"), OPENED: _("Opened"),
        DOWNLOADED: _("Downloaded"), RENAMED: _("Renamed"), MOVED: _("Moved"),
        MOVED_OUT: _("Moved to another bucket"), ENCRYPTED: _("Encrypted"),
        DECRYPTED: _("Encryption removed"), PUBLISHED: _("Made public"),
        UNPUBLISHED: _("Made private"), TRASHED: _("Moved to the trash"),
        RESTORED: _("Restored from the trash"), DELETED: _("Deleted"),
        PURGED: _("Deleted for good from the trash"), BUCKET_CREATED: _("The bucket was made"),
        BUCKET_CHANGED: _("The bucket's settings changed"),
        BUCKET_DELETE_REQUESTED: _("The bucket's deletion was asked for"),
        CLEARANCES_CHANGED: _("Clearances changed"), COPIED_OUT: _("Copied to another bucket"),
        COPIED_IN: _("Copied from another bucket"),
    }
    return [(kind, labels[kind]) for kind in FILE_KINDS + BUCKET_KINDS]
