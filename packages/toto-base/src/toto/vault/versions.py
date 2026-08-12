"""Saving, listing and restoring versions of a vault file.

The rule that shapes everything here: **a version is a conscious decision.**
Autosave writes the live file and records nothing. A version exists because a
person pressed a button and, usually, gave it a name. That is why this can be a
plain table rather than a delta-compressing engine — a document accumulates a
handful of versions over its life, not one per keystroke.

Three properties are load-bearing and each is enforced here rather than left to
callers:

* **Content-addressed.** Bodies are stored once per distinct digest. Restoring
  v3 and saving again writes no bytes at all. Documents in this platform embed
  their images as base64 inside the body, so an unchanged illustration is
  exactly the thing that must not be stored twice.
* **Append-only.** Restoring never rewinds the counter; it records a new version
  whose body happens to equal an old one. The ledger, the mint chain and
  ``ChainedRecord`` all work this way, and for the same reason: a history you
  can rewrite is not evidence of anything.
* **Pruning respects intent.** Unnamed versions are housekeeping and can go.
  A named one, or a rescued conflicting draft, never is — deleting those would
  break the only promise the feature makes.
"""

from __future__ import annotations

import hashlib

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils.translation import gettext_lazy as _

#: Unnamed versions kept per file. Named ones are never counted against it —
#: see prune(). Deliberately small: with versions as conscious acts, a file that
#: reaches this many unnamed ones is being used as an autosave log.
UNLABELLED_CAP = 20


class VersionError(Exception):
    """A version could not be written or restored, in words a user can read."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _blob_for(data: bytes):
    """The blob holding these exact bytes, written only if new.

    ``get_or_create`` on the digest is the whole dedupe. The race — two saves of
    identical content at once — resolves to whichever row lands first, because
    ``content_hash`` is unique and the loser simply re-reads it.
    """
    from django.db import IntegrityError

    from .models import VersionBlob

    content_hash = _digest(data)
    existing = VersionBlob.objects.filter(content_hash=content_hash).first()
    if existing is not None:
        return existing

    blob = VersionBlob(content_hash=content_hash, size_bytes=len(data))
    try:
        with transaction.atomic():
            blob.data.save(content_hash, ContentFile(data), save=False)
            blob.save()
    except IntegrityError:
        # Somebody wrote the same bytes between the check and the insert. Their
        # row is as good as ours, by definition — the digest is the identity.
        return VersionBlob.objects.get(content_hash=content_hash)
    return blob


def save_version(vault_file, *, body: bytes | str = None, author=None,
                 label: str = "", is_conflict: bool = False):
    """Record the file's current state as the next version.

    ``body`` defaults to whatever is in the vault file right now, which is the
    ordinary case: the editor has already autosaved and the user is naming that
    state. It is passed explicitly only when the bytes are NOT what is stored —
    the conflicting-draft path, where the losing writer's work never reached the
    file.
    """
    from .models import FileVersion

    if body is None:
        with vault_file.file.open("rb") as handle:
            body = handle.read()
    if isinstance(body, str):
        body = body.encode("utf-8")

    blob = _blob_for(body)

    with transaction.atomic():
        # Lock the file's existing versions so two concurrent saves cannot both
        # claim the same number. The unique constraint is the real guarantee —
        # this only stops it from being hit in the common case.
        last = (FileVersion.objects.select_for_update()
                .filter(file=vault_file).order_by("-number").first())
        version = FileVersion.objects.create(
            file=vault_file,
            number=(last.number + 1) if last else 1,
            blob=blob,
            label=(label or "").strip()[:200],
            author=author if getattr(author, "is_authenticated", False) else None,
            is_conflict=is_conflict,
        )
    prune(vault_file)
    return version


def save_conflicting_draft(vault_file, *, body, author=None):
    """Keep the work of a writer who lost an optimistic-concurrency check.

    The alternative — what happens today — is a 409 and the loser's edits gone.
    Nothing here merges anything: both bodies simply exist and a human decides.
    That is the answer Dropbox, OneDrive and Office co-authoring all landed on
    for documents that cannot be merged, and these cannot: cyprian's whole body
    is one CDATA line and primula's whole workbook is one JSON line.
    """
    who = getattr(author, "get_username", lambda: "")() or _("someone else")
    return save_version(
        vault_file, body=body, author=author,
        label=_("conflicting draft by %(who)s") % {"who": who},
        is_conflict=True)


def list_versions(vault_file):
    """Newest first, ready to render. Authors joined; bodies never loaded."""
    from .models import FileVersion

    return (FileVersion.objects.filter(file=vault_file)
            .select_related("author", "blob"))


def restore_version(version, *, actor=None):
    """Write an old body back over the live file, and record that as new.

    The counter moves FORWARD. Restoring v3 onto a file at v7 produces v8 whose
    body equals v3's — and because blobs are content-addressed, v8 reuses v3's
    blob and writes no bytes. Nothing between v3 and v7 is lost, which is the
    whole point of never rewinding.
    """
    vault_file = version.file
    data = version.read()

    with vault_file.file.open("wb") as handle:
        handle.write(data)
    vault_file.content_hash = _digest(data)
    vault_file.file_size_bytes = len(data)
    vault_file.save(update_fields=["content_hash", "file_size_bytes"])

    return save_version(
        vault_file, body=data, author=actor,
        label=_("restored from v%(number)s") % {"number": version.number})


def prune(vault_file) -> int:
    """Drop the oldest UNNAMED versions past the cap. Returns how many went.

    Named versions and rescued conflicting drafts are exempt and are not even
    counted — a file with thirty named versions keeps all thirty. Only the
    unnamed ones, which are housekeeping, are subject to a ceiling.

    Blobs outlive their versions until nothing cites them; the FK is PROTECT, so
    a shared blob can never be deleted out from under another version.
    """
    from .models import FileVersion, VersionBlob

    unlabelled = list(
        FileVersion.objects.filter(file=vault_file, label="", is_conflict=False)
        .order_by("-number").values_list("pk", "blob_id"))
    doomed = unlabelled[UNLABELLED_CAP:]
    if not doomed:
        return 0

    pks = [pk for pk, _blob in doomed]
    blob_ids = {blob for _pk, blob in doomed}
    FileVersion.objects.filter(pk__in=pks).delete()

    # Now-orphaned blobs only. Anything still cited by any version stays.
    for blob in VersionBlob.objects.filter(pk__in=blob_ids):
        if not blob.versions.exists():
            blob.data.delete(save=False)
            blob.delete()
    return len(pks)
