"""
Bundle selected vault files into a single ``.zip`` VaultFile.

Used by the ``vault_zip_files`` predefined workflow task (see predefined_tasks.py)
so archiving runs in a Celery worker, tracked as a WorkflowRun.
"""
from __future__ import annotations

import os
import tempfile
import zipfile

from django.core.files import File
from django.utils.text import slugify


def _unique_file_key(base_key: str, bucket) -> str:
    """A bucket-unique key, suffixed -1, -2, … until free (mirrors the helper in
    views.create_empty_vault_file)."""
    from toto.vault.models import VaultFile

    base_key = base_key or "file"
    key, counter = base_key, 1
    while VaultFile.objects.filter(bucket=bucket, key=key).exists():
        key = f"{base_key}-{counter}"
        counter += 1
    return key


def _arcname(vault_file, source_full_path: str, seen: set) -> str:
    """Path of *vault_file* relative to the source directory, de-duped."""
    dir_full = vault_file.directory.full_path() if vault_file.directory_id else ""
    rel_dir = dir_full[len(source_full_path):].lstrip("/") if source_full_path else dir_full
    name = vault_file.title or vault_file.key or f"file-{vault_file.pk}"
    arc = f"{rel_dir}/{name}" if rel_dir else name

    candidate, stem_n = arc, 1
    while candidate in seen:
        root, ext = os.path.splitext(arc)
        candidate = f"{root}-{stem_n}{ext}"
        stem_n += 1
    seen.add(candidate)
    return candidate


def zip_files_to_vault_file(owner, source_directory, target_directory, file_ids, output_name):
    """Stream the selected, non-encrypted files into a new ``zip`` VaultFile.

    ``source_directory`` provides the bucket and the root for relative arcnames.
    ``target_directory`` (may be ``None`` → bucket root) is where the archive is
    saved. Returns ``(vault_file, n_added)``. Raises ValueError when no eligible
    files are selected.
    """
    from toto.vault.models import VaultFile

    bucket = source_directory.bucket
    source_full = source_directory.full_path()

    files = list(
        VaultFile.objects.select_related("directory")
        .filter(pk__in=list(file_ids), bucket=bucket, is_encrypted=False)
    )
    if not files:
        raise ValueError("No eligible files selected to archive.")

    name = os.path.basename((output_name or "").strip()) or f"{source_directory.name}.zip"
    if not name.lower().endswith(".zip"):
        name += ".zip"

    seen: set = set()
    n_added = 0
    with tempfile.NamedTemporaryFile(suffix=".zip") as tmp:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for vf in files:
                arc = _arcname(vf, source_full, seen)
                with vf.file.open("rb") as src, zf.open(arc, "w") as dest:
                    for chunk in iter(lambda: src.read(1024 * 1024), b""):
                        dest.write(chunk)
                n_added += 1
        tmp.flush()
        tmp.seek(0)

        vault_file = VaultFile(
            owner=owner,
            title=name,
            key=_unique_file_key(slugify(os.path.splitext(name)[0]), bucket),
            file_type="zip",
            bucket=bucket,
            directory=target_directory,
            is_public=False,
        )
        vault_file.save()
        vault_file.file.save(name, File(tmp), save=True)

    try:
        vault_file.content_hash = vault_file.create_hash()
        vault_file.save(update_fields=["content_hash"])
    except Exception:
        pass
    return vault_file, n_added
