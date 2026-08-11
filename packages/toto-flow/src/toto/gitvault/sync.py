"""Bidirectional sync between a vault directory subtree and the repo's
materialized working tree (see git_integration.md).

Invariants:
- The vault DB is the source of truth BETWEEN git operations; the worktree is
  derived state, rebuilt by export and folded back by import.
- The GitRepoFile mapping is mutated only under the repo lock; export/import
  are idempotent (re-runs converge).
- Encrypted vault files are invisible to git (never exported).
- ``.git/`` is never touched by export nor read by import.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
from contextlib import contextmanager
from pathlib import Path

from django.core.files.base import ContentFile

from toto.vault.models import VaultDirectory, VaultFile
from toto.vault.storage_backends import get_bucket_storage

from .models import GitRepo, GitRepoFile


class RepoBusy(Exception):
    """Another operation holds this repo's lock."""


@contextmanager
def repo_lock(repo: GitRepo, timeout: bool = True):
    """Advisory flock on the repo dir — serializes export/import/git ops across
    the web and celery containers (same media volume, same host kernel)."""
    repo.base_dir.mkdir(parents=True, exist_ok=True)
    lock_path = repo.base_dir / "op.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (fcntl.LOCK_NB if timeout else 0))
        except BlockingIOError:
            raise RepoBusy("repository is busy with another operation")
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def subtree_directories(root: VaultDirectory) -> list[VaultDirectory]:
    """The directory subtree rooted at *root* (root included), BFS order."""
    dirs = [root]
    frontier = [root.pk]
    while frontier:
        children = list(VaultDirectory.objects.filter(parent_id__in=frontier))
        dirs.extend(children)
        frontier = [d.pk for d in children]
    return dirs


def subtree_files(root: VaultDirectory):
    dirs = subtree_directories(root)
    return (
        VaultFile.objects.filter(directory_id__in=[d.pk for d in dirs])
        .select_related("directory", "bucket")
        .order_by("pk")
    )


def _relpath_for(vault_file: VaultFile, root_full_path: str, seen: set) -> str:
    """Worktree-relative path: directory chain below the repo root + title,
    deduped like vault.archive._arcname (stable: files iterate in pk order)."""
    dir_full = vault_file.directory.full_path() if vault_file.directory_id else ""
    rel_dir = dir_full[len(root_full_path):].lstrip("/") if root_full_path else dir_full
    name = vault_file.title or vault_file.key or f"file-{vault_file.pk}"
    rel = f"{rel_dir}/{name}" if rel_dir else name

    candidate, n = rel, 1
    while candidate in seen:
        stem, ext = os.path.splitext(rel)
        candidate = f"{stem}-{n}{ext}"
        n += 1
    seen.add(candidate)
    return candidate


def _prune_empty_dirs(worktree: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(worktree, topdown=False):
        p = Path(dirpath)
        if p == worktree or ".git" in p.relative_to(worktree).parts:
            continue
        if not dirnames and not filenames:
            p.rmdir()


def export_worktree(repo: GitRepo) -> dict:
    """vault DB → worktree. Returns {"written": [...], "removed": [...]}."""
    root = repo.directory
    root_full = root.full_path()
    worktree = repo.worktree
    worktree.mkdir(parents=True, exist_ok=True)

    written, seen_paths = [], set()
    current_pks = set()
    mappings = {m.vault_file_id: m for m in repo.files.all()}

    for vf in subtree_files(root):
        if vf.is_encrypted:
            continue
        current_pks.add(vf.pk)
        relpath = _relpath_for(vf, root_full, seen_paths)

        mapping = mappings.get(vf.pk)
        if mapping is None:
            mapping = GitRepoFile(repo=repo, vault_file=vf, relpath=relpath)

        dest = worktree / relpath
        try:
            content = get_bucket_storage(vf.bucket).read(vf.file.name)
        except Exception:
            # Missing bytes (e.g. storage drift) — skip rather than fail the op.
            continue

        content_hash = _sha256(content)
        # Keep the vault-side hash honest — several write paths (notably the
        # web editor) skip recomputing it, and import's change detection
        # relies on it matching the last exported bytes.
        if vf.content_hash != content_hash:
            vf.content_hash = content_hash
            vf.save(update_fields=["content_hash"])

        if not dest.is_file() or _sha256(dest.read_bytes()) != content_hash:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
            written.append(relpath)

        if mapping.pk is None or mapping.relpath != relpath:
            mapping.relpath = relpath
            mapping.save()

    # Stale mappings: the vault file still exists but moved out of the subtree.
    # (Deleted vault files take their mapping row with them via CASCADE, which
    # is why orphan detection below walks the WORKTREE, not the mappings.)
    repo.files.exclude(vault_file_id__in=current_pks).delete()

    # Any worktree file not produced by this export is an orphan — its vault
    # counterpart was deleted, renamed or moved away.
    removed = []
    for dirpath, dirnames, filenames in os.walk(worktree):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fname in filenames:
            full = Path(dirpath) / fname
            relpath = str(full.relative_to(worktree))
            if relpath not in seen_paths:
                full.unlink()
                removed.append(relpath)

    _prune_empty_dirs(worktree)
    return {"written": written, "removed": sorted(removed)}


def _ensure_directory_chain(repo: GitRepo, rel_dir: str) -> VaultDirectory:
    """VaultDirectory for *rel_dir* below the repo root, creating segments as
    needed (ingress_vault-style get_or_create)."""
    node = repo.directory
    if not rel_dir:
        return node
    for segment in rel_dir.split("/"):
        node, _ = VaultDirectory.objects.get_or_create(
            bucket=node.bucket, parent=node, name=segment,
            defaults={"owner": repo.owner},
        )
    return node


def import_worktree(repo: GitRepo) -> dict:
    """worktree → vault DB after pull/merge/checkout.
    Returns {"created": [...], "updated": [...], "deleted": [...]}."""
    worktree = repo.worktree
    by_relpath = {m.relpath: m for m in repo.files.select_related("vault_file")}

    created, updated = [], []
    present = set()

    for dirpath, dirnames, filenames in os.walk(worktree):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fname in filenames:
            full = Path(dirpath) / fname
            relpath = str(full.relative_to(worktree))
            present.add(relpath)
            content = full.read_bytes()

            mapping = by_relpath.get(relpath)
            if mapping is None:
                from django.utils.text import slugify

                from toto.vault.archive import _unique_file_key

                rel_dir = os.path.dirname(relpath)
                directory = _ensure_directory_chain(repo, rel_dir)
                vf = VaultFile(
                    owner=repo.owner,
                    title=fname,
                    # VaultFile.save() raises on a duplicate auto-slugged key,
                    # so pre-assign a bucket-unique one.
                    key=_unique_file_key(
                        slugify(os.path.splitext(fname)[0]), repo.directory.bucket
                    ),
                    bucket=repo.directory.bucket,
                    directory=directory,
                    file_type=VaultFile.detect_type("", filename=fname),
                )
                vf.file.save(fname, ContentFile(content), save=False)
                vf.content_hash = _sha256(content)
                vf.save()
                GitRepoFile.objects.create(repo=repo, vault_file=vf, relpath=relpath)
                created.append(relpath)
            else:
                vf = mapping.vault_file
                if vf.content_hash != _sha256(content) or not vf.content_hash:
                    with vf.file.open("wb") as fh:
                        fh.write(content)
                    vf.content_hash = _sha256(content)
                    vf.file_size_bytes = len(content)
                    vf.save()
                    updated.append(relpath)

    # Paths tracked in the mapping but no longer in the worktree → a pulled /
    # merged deletion; delete the vault file (post_delete removes the bytes).
    deleted = []
    for relpath, mapping in by_relpath.items():
        if relpath in present:
            continue
        mapping.vault_file.delete()  # cascades to the mapping row
        deleted.append(relpath)

    return {"created": created, "updated": updated, "deleted": deleted}


def nesting_conflict(directory: VaultDirectory) -> GitRepo | None:
    """A repo on an ancestor or descendant of *directory* (or itself)."""
    node = directory
    while node is not None:
        repo = GitRepo.objects.filter(directory=node).first()
        if repo:
            return repo
        node = node.parent
    subtree_ids = [d.pk for d in subtree_directories(directory)]
    return GitRepo.objects.filter(directory_id__in=subtree_ids).first()
