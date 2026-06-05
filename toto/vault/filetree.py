"""
Reusable file-tree picker for vault-backed apps (manta, transcription, ocr).

Provides an access-checked query + a grouped (bucket → directory → files) tree
structure, rendered by the ``vault/_file_tree.html`` partial with checkboxes.
This keeps the "pick files from a tree like the storage app" UI in one place.
"""

from __future__ import annotations

import mimetypes

from django.db.models import Q


def accessible_files(user, *, file_types=None, bucket=None, exclude_pk=None):
    """VaultFiles the user may read, optionally scoped to a bucket / type."""
    from toto.vault.models import VaultFile

    qs = VaultFile.objects.select_related("bucket", "directory")
    if bucket is not None:
        qs = qs.filter(bucket=bucket)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    if file_types:
        qs = qs.filter(file_type__in=file_types)

    if getattr(user, "is_superuser", False):
        return qs
    return qs.filter(
        Q(owner=user)
        | Q(is_public=True)
        | Q(bucket__owner=user)
        | Q(directory__allowed_users=user)
    ).distinct()


def _row(f) -> dict:
    mime, _ = mimetypes.guess_type(f.title or "")
    return {
        "id": f.id,
        "title": f.title,
        "key": f.key,
        "file_type": f.file_type,
        "mimetype": mime or "",
        "size": f.file_size_bytes,
    }


def build_file_tree(user, *, file_types=None, bucket=None, exclude_pk=None, limit=500):
    """Grouped tree: ``[{bucket, groups: [{dir, files: [row,...]}]}]``.

    Files are grouped by bucket, then by directory path (``""`` = bucket root).
    """
    files = (
        accessible_files(user, file_types=file_types, bucket=bucket, exclude_pk=exclude_pk)
        .order_by("bucket__name", "title")[:limit]
    )

    buckets: dict = {}
    for f in files:
        bnode = buckets.setdefault(f.bucket_id, {"bucket": f.bucket, "dirs": {}})
        dlabel = f.directory.full_path() if f.directory_id else ""
        bnode["dirs"].setdefault(dlabel, []).append(_row(f))

    out = []
    for bnode in buckets.values():
        groups = [{"dir": d, "files": rows} for d, rows in sorted(bnode["dirs"].items())]
        out.append({"bucket": bnode["bucket"], "groups": groups})
    out.sort(key=lambda b: (b["bucket"].name if b["bucket"] else ""))
    return out
