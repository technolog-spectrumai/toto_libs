"""A private vault bucket and Images directory for each poll thread."""

from __future__ import annotations

from django.db import transaction


def ensure(poll, owner):
    from toto.vault.models import Bucket, StorageBackend, VaultDirectory

    bucket = poll.bucket
    if bucket is None or bucket.is_being_deleted:
        bucket = Bucket.objects.create(
            owner=None, created_by=owner, slug=f"forum-poll-{poll.pk}",
            name=f"Forum poll {poll.pk}", storage_backend=StorageBackend.LOCAL)
        poll.bucket = bucket
        poll.save(update_fields=["bucket"])
    directory = poll.directory
    if directory is None or directory.bucket_id != bucket.pk:
        directory = VaultDirectory.objects.create(
            bucket=bucket, owner=owner, name="Images")
        poll.directory = directory
        poll.save(update_fields=["directory"])
    return bucket, directory


def empty(poll):
    """Remove a poll's storage after its linked vault files have been purged."""
    from toto.vault.models import VaultFile

    if poll.bucket_id is None:
        return
    if VaultFile.all_objects.filter(bucket_id=poll.bucket_id).exists():
        return
    bucket = poll.bucket
    if poll.directory_id:
        poll.directory.delete()
    poll.directory = None
    poll.bucket = None
    poll.save(update_fields=["directory", "bucket"])
    bucket.delete()
