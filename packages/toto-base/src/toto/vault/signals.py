import os
from django.db.models.signals import post_delete
from django.dispatch import Signal, receiver
from toto.vault.models import VaultFile

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
