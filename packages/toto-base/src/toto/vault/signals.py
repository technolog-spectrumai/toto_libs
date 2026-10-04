import os
from django.db.models.signals import post_delete, post_save
from django.dispatch import Signal, receiver
from toto.vault.models import VaultFile, live_state

#: Sent once a file has actually been encrypted (``VaultFile.encrypt``, the one
#: method all four encrypt doors call). Keyword argument: ``file``.
#:
#: A signal rather than a call because the listeners live in wheels toto-base
#: may not import — ``toto.mana`` rewards the act in security mana — and a
#: signal is an edge that points the allowed way. Sent with ``send_robust``: a
#: finished encryption must never turn into an error because a listener failed.
file_encrypted = Signal()

#: Sent by the transfer runner once per file it lands, as the LAST statement of
#: that file's landing transaction. Keyword arguments: ``run`` (the
#: TransferRun), ``source_file``, ``new_file`` (the new VaultFile, or None when
#: the destination is another host) and ``dest_key`` (the key the copy got
#: here, or the one the far host assigned).
#:
#: Sent with ``send``, not ``send_robust``, and that is the contract: what a
#: listener writes commits with the landed row and the run's cursor, or not at
#: all. A listener that raises stops the run — the file's row is rolled back,
#: its bytes deleted, and the run fails naming the listener's error. A host's
#: sync app (zenobia's yamabiko) records its copies here; nothing in toto-base
#: listens.
transfer_file_landed = Signal()


#: Sent after a file's row changed in a way a listing shows (2026-10-04).
#: Keyword arguments: ``file`` (the VaultFile), ``kind`` and ``was`` — the
#: row's ``models.LIVE_FIELDS`` as it stood before, ``{}`` for a new file.
#: ``kind`` is one of:
#:
#: * ``uploaded`` — the row was made (an upload, a landed transfer, a zip, a
#:   mirror stub);
#: * ``replaced`` — its bytes changed (a saved version put back, a client's
#:   push): the content hash moved from one value to another;
#: * ``changed`` — its name, type, lock or public flag changed;
#: * ``moved`` — its folder or its bucket changed;
#: * ``trashed`` / ``restored`` — into the trash and back;
#: * ``deleted`` — a live row deleted outright (a mounted remote bucket's
#:   file, a purged bucket). A trashed row's purge is not sent: it left the
#:   listing when it was trashed.
#:
#: From the model's own signals, so every writer is covered — the page, the
#: JSON API, the admin, a worker — and no door had to learn it. Sent with
#: ``send_robust``: a listener that fails never fails the save. What listens:
#: ``toto.vault.live`` (the folders that update as they change) and
#: ``toto.notify.sources`` (the bell), each only where it is installed.
file_changed = Signal()


def _attnames(update_fields):
    """``save(update_fields=…)``'s names as column attributes
    (``directory`` → ``directory_id``), or None for a whole save."""
    if update_fields is None:
        return None
    names = set()
    for name in update_fields:
        try:
            names.add(VaultFile._meta.get_field(name).attname)
        except Exception:  # noqa: BLE001 - a name the model lacks changes nothing
            continue
    return names


def changes(created, was, now, saved=None) -> list:
    """The ``file_changed`` kinds one save amounts to. ``was`` and ``now``
    are ``live_state`` dicts; a column either lacks (deferred) is not
    compared, and with ``saved`` only the columns that were written are."""
    if created:
        return [] if now.get("trashed_at") is not None else ["uploaded"]
    if not was:
        return []

    def differs(name):
        if saved is not None and name not in saved:
            return False
        return name in was and name in now and was[name] != now[name]

    if differs("trashed_at") and (was["trashed_at"] is None) != (now["trashed_at"] is None):
        return ["trashed" if now["trashed_at"] is not None else "restored"]
    if now.get("trashed_at") is not None:
        return []
    kinds = []
    if differs("directory_id") or differs("bucket_id"):
        kinds.append("moved")
    if differs("content_hash") and was["content_hash"] and now["content_hash"]:
        kinds.append("replaced")
    if any(differs(name) for name in ("title", "file_type", "is_encrypted", "is_public")):
        kinds.append("changed")
    return kinds


@receiver(post_save, sender=VaultFile, dispatch_uid="toto.vault.file_changed.saved")
def announce_saved(sender, instance, created, raw=False, update_fields=None, **kwargs):
    if raw:
        return
    was = getattr(instance, "_loaded_live", None) or {}
    saved = _attnames(update_fields)
    now = live_state(instance)
    # The next save of this same object compares with what THIS one wrote.
    instance._loaded_live = ({**was, **{k: v for k, v in now.items() if k in saved}}
                             if saved is not None and not created else now)
    for kind in changes(created, was, now, saved):
        _announce(instance, kind, dict(was))


@receiver(post_delete, sender=VaultFile, dispatch_uid="toto.vault.file_changed.deleted")
def announce_deleted(sender, instance, **kwargs):
    if getattr(instance, "trashed_at", None) is not None:
        return
    _announce(instance, "deleted", live_state(instance))


def _announce(instance, kind, was):
    for listener, answer in file_changed.send_robust(
            sender=VaultFile, file=instance, kind=kind, was=was):
        if isinstance(answer, Exception):
            import logging

            # The class only: a listener's error text can carry a file's name.
            logging.getLogger("toto.vault").warning(
                "vault: a listener of file_changed failed on %s (%s)", kind,
                type(answer).__name__)


@receiver(post_delete, sender=VaultFile)
def delete_file_on_disk(sender, instance, **kwargs):
    """Deletes the physical file when a VaultFile record is removed.

    Never for a mirror stub: its ``file.name`` is the PEER's key, and a key
    that happens to spell a relative path on this disk would unlink another
    file's bytes. Deleting a stub (a refresh's prune, a mount's delete) is a
    row delete and nothing else.
    """
    if getattr(instance, "origin", "") == "mirror":
        return
    if getattr(instance, "trashed_at", None) is not None:
        # A trashed row's bytes are what the restore gives back (2026-10-01),
        # so a delete of that row never unlinks them while its transaction
        # can still roll back: only once it commits. ``purge_file`` — Delete
        # for good, the nightly purge, a bucket purge — deletes them itself
        # through the bucket driver; this after-commit unlink is for the
        # cascades that bypass it (an erased account), so they leave no orphan.
        if instance.file:
            from django.db import transaction

            path = instance.file.path
            transaction.on_commit(lambda: _unlink(path))
        return
    if instance.file and os.path.isfile(instance.file.path):
        _unlink(instance.file.path)


def _unlink(path: str) -> None:
    if not os.path.isfile(path):
        return
    try:
        os.remove(path)
    except Exception as e:
        print(f"Error deleting file {path}: {e}")
