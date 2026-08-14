"""The room's document library: the vault, scoped to one channel.

Not a second filesystem — the Business Center precedent restated for rooms:
one platform bucket (slug ``forum``), one ``VaultDirectory`` per channel, one
``FileGateway`` in front of it. Uploads go through the vault's own gateway
pages, so quota, charging and antivirus screening all apply without this app
carrying any upload machinery of its own.

**The whitelist is the isolation.** Vault treats an EMPTY ``allowed_users``
as "every authenticated user", so a fresh directory must never sit with an
empty list — the bucket owner is always on it. Membership changes re-sync
through the same signal that already tells live sockets, so leaving a room
revokes the library the same moment it revokes the chat.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

FORUM_BUCKET_SLUG = "forum"


def ensure_forum_bucket(*, owner=None):
    from django.contrib.auth import get_user_model

    from toto.vault.models import Bucket

    bucket = Bucket.objects.filter(slug=FORUM_BUCKET_SLUG).first()
    if bucket:
        return bucket
    if owner is None:
        owner = (get_user_model().objects.filter(is_superuser=True)
                 .order_by("pk").first())
    if owner is None:
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured(
            "The forum bucket needs an owner and no superuser exists.")
    return Bucket.objects.create(slug=FORUM_BUCKET_SLUG, name="Forum",
                                 owner=owner)


def ensure_channel_library(channel, *, owner=None):
    """The room's directory and gateway, created once, synced always."""
    from toto.vault.models import FileGateway, VaultDirectory

    bucket = ensure_forum_bucket(owner=owner)
    directory, _ = VaultDirectory.objects.get_or_create(
        bucket=bucket, parent=None, name=channel.slug,
        defaults={"owner": bucket.owner})
    FileGateway.objects.get_or_create(
        directory=directory,
        defaults={"name": f"forum-{channel.slug}", "bucket": bucket})
    if channel.vault_directory_id != directory.pk:
        channel.vault_directory = directory
        channel.save(update_fields=["vault_directory"])
    sync_channel_library_users(channel)
    return directory


def sync_channel_library_users(channel) -> None:
    """Whitelist = active members + the bucket owner. Never empty."""
    directory = channel.vault_directory
    if directory is None:
        return
    ids = {
        uid for uid in channel.forum_members.filter(is_active=True)
        .values_list("person__user_id", flat=True) if uid
    }
    ids.add(directory.bucket.owner_id)  # never leave the whitelist empty
    directory.allowed_users.set(ids)
    gateway = getattr(directory, "gateway", None)
    if gateway is not None:
        gateway.allowed_users.set(ids)


def library_files(channel):
    """This room's files, and nothing else."""
    from toto.vault.models import VaultFile

    if channel.vault_directory_id is None:
        return VaultFile.objects.none()
    return VaultFile.objects.filter(directory_id=channel.vault_directory_id)
