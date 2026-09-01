"""Workspace and file transitions.

Keyword-only functions raising `ValidationError` with a sentence a person can
read, the bourse convention. Views hold no logic.
"""

from __future__ import annotations

import posixpath

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils.text import slugify

from toto.vault.models import Bucket, VaultDirectory, VaultFile

from .models import AmbrosiaSettings, ExecutionMode, Workspace, WorkspaceKind

STARTER = {
    "python": '"""A new workspace."""\n\n\nprint("hello from ambrosia")\n',
    "latex": "\\documentclass{article}\n\\begin{document}\nHello.\n\\end{document}\n",
    "text": "",
    "json": "{}\n",
    "yaml": "---\n",
    "csv": "",
    "html": "<!doctype html>\n<html>\n  <body></body>\n</html>\n",
    "xml": "<root></root>\n",
    "bib": "",
}

# What "New file" offers inside a workspace. Extension decides the vault type,
# which is what makes .py open in Ambrosia and .tex in the LaTeX editor.
EXTENSION_TYPES = {
    ".py": "python",
    ".txt": "text",
    ".md": "text",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".csv": "csv",
    ".html": "html",
    ".xml": "xml",
    ".tex": "latex",
    ".sty": "latex",
    ".cls": "latex",
    ".bib": "bib",
    ".bst": "bib",
}


def file_type_for(filename: str, default: str = "text") -> str:
    _, _, ext = filename.rpartition(".")
    return EXTENSION_TYPES.get(f".{ext.lower()}", default) if ext else default


def _unique_key(base: str, bucket) -> str:
    """A bucket-unique file key. VaultFile.save() raises on a collision."""
    key, n = base or "file", 1
    while VaultFile.objects.filter(bucket=bucket, key=key).exists():
        n += 1
        key = f"{base}-{n}"
    return key


def buckets_for(user):
    """Buckets a user may put a workspace in — the ones they own."""
    return Bucket.objects.filter(owner=user).order_by("name")


def directories_for(bucket):
    """Every folder in a bucket, for the 'where should this live' picker."""
    return VaultDirectory.objects.filter(bucket=bucket).order_by("name")


@transaction.atomic
def create_workspace(*, owner, name: str, bucket, directory=None,
                     new_directory_name: str = "",
                     kind: str = WorkspaceKind.PYTHON, description: str = "",
                     execution: str = ExecutionMode.KERNEL) -> Workspace:
    """Make a workspace inside an existing bucket.

    Ambrosia creates no buckets. The caller picks one they own and either points
    at a folder that is already there or names a new one, which is created under
    `directory` (or at the bucket root when that is None).

    The workspace IS that folder: everything it can see, create or destroy is
    bounded by it, so one bucket can hold many workspaces without them ever
    seeing each other.
    """
    name = (name or "").strip()
    if not name:
        raise ValidationError("A workspace needs a name.")
    if bucket is None:
        raise ValidationError("Choose a bucket for this workspace to live in.")
    if bucket.owner_id != owner.pk:
        raise ValidationError("That bucket is not yours.")
    if directory is not None and directory.bucket_id != bucket.pk:
        raise ValidationError("That folder is not in the bucket you chose.")

    # The per-user cap, checked HERE and not in the view: every lab creates its
    # workspaces through this function, so a guard in one form would be a guard
    # one new caller goes round. It is also checked BEFORE the block below,
    # which may create a VaultDirectory — a refused creation must leave no
    # folder behind in somebody's bucket.
    cap = AmbrosiaSettings.workspace_cap()
    if cap:
        held = Workspace.objects.filter(owner=owner).count()
        if held >= cap:
            raise ValidationError(
                f"You are holding {held} workspaces, which is the limit on "
                f"this platform. Destroy one to make another."
            )

    new_directory_name = (new_directory_name or "").strip().strip("/")
    if new_directory_name:
        if "/" in new_directory_name:
            raise ValidationError("A folder name cannot contain a path.")
        if VaultDirectory.objects.filter(
                bucket=bucket, parent=directory, name=new_directory_name).exists():
            raise ValidationError(
                f"“{new_directory_name}” already exists in that folder.")
        root = VaultDirectory.objects.create(
            name=new_directory_name[:200], bucket=bucket, owner=owner,
            parent=directory)
    elif directory is not None:
        root = directory
    else:
        # Neither given. Rather than quietly adopting the whole bucket root —
        # which would make the workspace's tree the entire bucket and its destroy
        # button catastrophic — make the caller say where it goes.
        raise ValidationError(
            "Choose an existing folder, or give a name for a new one.")

    if hasattr(root, "ambrosia_workspace"):
        raise ValidationError(
            f"“{root.name}” is already the folder of another workspace.")

    workspace = Workspace.objects.create(
        name=name, owner=owner, kind=kind, execution=execution,
        description=description, bucket=bucket, root_directory=root,
    )

    # Only seed a starter file into a folder that is otherwise empty. Adopting a
    # folder that already has code in it and dropping a main.py on top would be
    # presumptuous.
    if not VaultFile.objects.filter(directory=root).exists():
        seed = create_file(
            workspace=workspace, user=owner,
            filename="main.py" if kind == WorkspaceKind.PYTHON else "main.tex",
            directory=root,
        )
        if kind == WorkspaceKind.LATEX:
            # The seeded document is the main document until someone picks
            # another — texlab reads the field; the base only sets the obvious
            # default at birth.
            workspace.main_file = seed
            workspace.save(update_fields=["main_file"])
    return workspace


@transaction.atomic
def destroy_workspace(*, workspace, user) -> dict:
    """Delete the workspace AND its folder, with everything inside it.

    Irreversible, and the counterpart to `close_workspace`, which keeps the
    files. Returns what was removed so the caller can say so.

    The order matters. `VaultFile.directory` is **SET_NULL**, so deleting the
    directory first would not delete its files — it would quietly spill every one
    of them into the bucket root, where they would reappear in the vault browser
    as loose files nobody meant to keep. Files go first, explicitly, one queryset
    delete so vault's post_delete signal fires per row and removes each blob from
    disk. Subdirectories then go with the root by CASCADE.
    """
    if workspace.owner_id != user.pk:
        raise ValidationError("Only the owner can destroy a workspace.")

    root = workspace.root_directory
    scope = workspace.directory_ids()

    files = VaultFile.objects.filter(directory_id__in=scope)
    file_count = files.count()
    files.delete()

    folder_count = max(0, len(scope) - 1)
    name = workspace.name
    # Deleting the root cascades to its subdirectories and, through the workspace's
    # own CASCADE on root_directory, to the workspace row itself.
    if root is not None:
        root.delete()
    else:
        workspace.delete()

    return {"name": name, "files": file_count, "folders": folder_count}


def update_settings(*, workspace, namespace, values, may_execute=True) -> dict:
    """Store one language app's settings, validated against its declaration.

    The base never learns what a key means: the lab that owns `namespace`
    declares its fields through the registry, `settings_spec.clean` validates
    against that, and only declared keys survive. Returns the settings in force
    afterwards (stored values re-clamped), which is what the panel re-renders.
    """
    from . import registry, settings_spec

    app = registry.for_namespace(namespace)
    fields = app.settings_fields() if app else ()
    if not fields:
        raise ValidationError("This workspace has nothing to configure.")

    cleaned = settings_spec.clean(
        fields, values or {}, workspace=workspace, may_execute=may_execute)

    stored = dict(workspace.settings or {})
    section = dict(stored.get(namespace) or {})
    for key, value in cleaned.items():
        # None means "use the host default" — drop the override rather than
        # freezing today's default into the row, where a later change to the
        # host setting would never reach this workspace.
        if value is None:
            section.pop(key, None)
        else:
            section[key] = value
    stored[namespace] = section
    workspace.settings = stored
    workspace.save(update_fields=["settings", "updated_at"])

    return settings_spec.effective(fields, section, workspace=workspace)


def reset_settings(*, workspace, namespace) -> dict:
    """Drop every override for one language app, back to the host defaults."""
    from . import registry, settings_spec

    stored = dict(workspace.settings or {})
    stored.pop(namespace, None)
    workspace.settings = stored
    workspace.save(update_fields=["settings", "updated_at"])

    app = registry.for_namespace(namespace)
    fields = app.settings_fields() if app else ()
    return settings_spec.effective(fields, {}, workspace=workspace)


@transaction.atomic
def close_workspace(*, workspace, user) -> str:
    """Forget the workspace, leave every file where it is."""
    if workspace.owner_id != user.pk:
        raise ValidationError("Only the owner can close a workspace.")
    name = workspace.name
    workspace.delete()
    return name


@transaction.atomic
def create_file(*, workspace, user, filename: str, directory=None) -> VaultFile:
    filename = (filename or "").strip().strip("/")
    if not filename:
        raise ValidationError("A file needs a name.")
    if "/" in filename or filename in (".", ".."):
        raise ValidationError("A file name cannot contain a path.")

    directory = _resolve_directory(workspace, directory)
    file_type = file_type_for(filename, workspace.default_file_type)

    if VaultFile.objects.filter(bucket=workspace.bucket, directory=directory,
                                title=filename).exists():
        raise ValidationError(f"“{filename}” already exists here.")

    vault_file = VaultFile(
        owner=user, title=filename,
        key=_unique_key(slugify(posixpath.splitext(filename)[0]) or "file",
                        workspace.bucket),
        file_type=file_type, bucket=workspace.bucket, directory=directory,
        is_public=False,
    )
    vault_file.save()
    vault_file.file.save(
        filename, ContentFile(STARTER.get(file_type, "").encode("utf-8")), save=True)
    vault_file.content_hash = vault_file.create_hash()
    vault_file.save()
    return vault_file


@transaction.atomic
def create_directory(*, workspace, user, name: str, parent=None) -> VaultDirectory:
    name = (name or "").strip().strip("/")
    if not name:
        raise ValidationError("A folder needs a name.")
    if "/" in name:
        raise ValidationError("A folder name cannot contain a path.")

    parent = _resolve_directory(workspace, parent)
    if VaultDirectory.objects.filter(bucket=workspace.bucket, parent=parent,
                                     name=name).exists():
        raise ValidationError(f"“{name}” already exists here.")
    return VaultDirectory.objects.create(
        name=name, bucket=workspace.bucket, owner=user, parent=parent)


def rename_file(*, workspace, vault_file, name: str) -> VaultFile:
    name = (name or "").strip()
    if not name or "/" in name:
        raise ValidationError("That is not a valid file name.")
    vault_file.title = name
    # The extension decides which editor opens it, so renaming .txt to .py has
    # to move the file type too or the rename is a lie.
    vault_file.file_type = file_type_for(name, vault_file.file_type)
    vault_file.save(update_fields=["title", "file_type"])
    return vault_file


def delete_file(*, workspace, vault_file) -> None:
    vault_file.delete()


# What the editor will load in one go. A pdfTeX log on a document with a
# thousand overfull boxes runs to megabytes, and streaming that into ACE hangs
# the tab; the tail is also the part anyone reads, since errors accumulate.
MAX_READ_BYTES = 512 * 1024


def read_file(vault_file) -> tuple[str, bool]:
    """The file's text, and whether it was truncated.

    Returns a pair rather than raising on a big file: refusing to open a 40 MB
    log is not better than opening the end of it, which is where the errors are.
    """
    if vault_file.is_encrypted:
        raise ValidationError("This file is encrypted and cannot be edited here.")

    with vault_file.file.open("rb") as fh:
        size = vault_file.file.size
        if size <= MAX_READ_BYTES:
            try:
                return fh.read().decode("utf-8"), False
            except UnicodeDecodeError:
                raise ValidationError("This file is not UTF-8 text.")

        # Seek to the tail — errors accumulate at the END of a TeX log, so the
        # last half-megabyte is the useful half. A storage backend that cannot
        # seek gets the head instead, and the banner says which one you got.
        tail = True
        try:
            fh.seek(size - MAX_READ_BYTES)
        except (AttributeError, OSError, ValueError):
            tail = False
        raw = fh.read(MAX_READ_BYTES)

    if tail:
        # Trim to the next newline so the first visible line is a whole one.
        newline = raw.find(b"\n")
        if newline != -1:
            raw = raw[newline + 1:]
        banner = "… earlier output trimmed …\n"
    else:
        raw = raw.rpartition(b"\n")[0] or raw
        banner = "… the rest of this file is not shown …\n"
    # errors="replace": a cut can land in the middle of a codepoint.
    return banner + raw.decode("utf-8", errors="replace"), True


def write_file(*, vault_file, content: str) -> VaultFile:
    from toto.vault.models import file_edits_allowed

    if not file_edits_allowed():
        raise ValidationError("File editing is switched off on this platform.")
    if vault_file.is_encrypted:
        raise ValidationError("This file is encrypted and cannot be edited here.")
    vault_file.file.save(
        vault_file.title, ContentFile((content or "").encode("utf-8")), save=True)
    vault_file.content_hash = vault_file.create_hash()
    vault_file.file_size_bytes = vault_file.file.size
    vault_file.save(update_fields=["content_hash", "file_size_bytes"])
    return vault_file


def _resolve_directory(workspace, directory):
    """None means the workspace root; anything else must be INSIDE the workspace.

    Checking the bucket is not enough any more. A bucket can hold several
    workspaces, so a bucket-only check would let one workspace create files in
    another's folder just by passing its pk.
    """
    if directory is None:
        return workspace.root_directory
    if directory.pk not in set(workspace.directory_ids()):
        raise ValidationError("That folder is not part of this workspace.")
    return directory
