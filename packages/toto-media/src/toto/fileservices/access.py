"""
Vault-file access policy shared by file-service apps (manta, transcription).
A user may read a VaultFile when they are a superuser, own it, it is
public, they own its bucket, or a directory sharing whitelist grants access.
"""

from __future__ import annotations


def user_can_access_vault_file(user, vf) -> bool:
    if vf is None:
        return False
    if user is None or not getattr(user, "is_authenticated", False):
        return bool(vf.is_public)
    if user.is_superuser:
        return True
    if vf.owner_id == user.id:
        return True
    if vf.is_public:
        return True
    if vf.bucket_id and vf.bucket.owner_id == user.id:
        return True
    if vf.directory_id and vf.directory.allowed_users.filter(pk=user.pk).exists():
        return True
    return False
