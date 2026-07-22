"""
Access helpers for the manta command builder.

These re-implement the vault's access policy in one place so the builder can
*independently* verify that a user may read a given VaultFile — both the source
file and every file referenced as an extra input — without trusting that the
caller (e.g. the vault wand) already checked.

Policy (a user may read a VaultFile when):
  * they are a superuser, or
  * they own the file, or
  * the file is public, or
  * they own the file's bucket, or
  * a directory sharing whitelist explicitly grants them access.

A file that is private, not owned by the user, in a bucket they do not own and
not shared via a directory whitelist is **not** accessible — matching the
vault's own listing rules.
"""

from __future__ import annotations

from django.db.models import Q

# Canonical implementation lives in fileservices so transcription can share it.
from toto.fileservices.access import user_can_access_vault_file  # noqa: F401


def accessible_bucket_files(user, bucket, *, exclude_pk=None, file_types=None):
    """Same-bucket VaultFiles the user may reference as inputs.

    Returns a QuerySet (possibly empty). Used both to render the bucket listing
    and to validate user-supplied file references.
    """
    from toto.vault.models import VaultFile

    if bucket is None:
        return VaultFile.objects.none()

    qs = VaultFile.objects.filter(bucket=bucket).select_related("directory", "bucket")
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    if file_types:
        qs = qs.filter(file_type__in=file_types)

    if user.is_superuser or bucket.owner_id == user.id:
        return qs

    shared_dir_ids = list(
        bucket.directories.filter(allowed_users=user).values_list("id", flat=True)
    )
    return qs.filter(
        Q(owner=user) | Q(is_public=True) | Q(directory_id__in=shared_dir_ids)
    )
