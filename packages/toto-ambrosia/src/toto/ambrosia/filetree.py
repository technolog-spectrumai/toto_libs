r"""Flattening a workspace into the shape the sidebar renders.

The idiom is lifted from vault's browser, which is the good part of a 2,200-line
template: a pre-ordered depth-first list where every row carries a `depth`
integer and a parent id, rather than nested DOM. Indentation is padding, and
visibility is a parent walk. It is O(n), handles arbitrary nesting, and lets the
client filter and sort without rebuilding a tree.

**The bucket is the boundary.** The tree shows everything in the workspace's
bucket — sources, images, generated logs, a neighbouring workspace's folder —
and nothing outside it, ever.

That reverses an earlier decision to scope the tree to the workspace's own
folder, and the reversal is deliberate. Folder scoping fixed a real leak, but at
the wrong level: it also meant a shared `preamble.sty` at the bucket root was
invisible to the editor AND never staged for a compile, so `\usepackage` failed
with no clue why. The bucket is the thing you own; a workspace is a view onto it
rooted at a folder.

What stays folder-scoped is destruction — `destroy_workspace` still deletes only
the workspace's own directory, and must never gain the power to empty a bucket.
"""

from __future__ import annotations

from toto.vault.models import VaultDirectory, VaultFile

# Only these open in the editor. Everything else in the bucket is still listed —
# a workspace that hid its own data files would be lying about its contents —
# but it opens read-only, or downloads.
EDITABLE_TYPES = {
    "python", "text", "json", "yaml", "xml", "csv", "html", "latex", "bib",
    # A deck is text (XML) and was editable here as "xml" before it had a class
    # of its own; leaving it out would make it listed-but-unopenable, which is
    # exactly the lie the comment above disclaims.
    "pxml", "presentation",
}

# Not editable, but the room can still show them: a compiled PDF opens in the
# preview pane rather than downloading. Listing an output file and then refusing
# to do anything with it would be the worst of both.
VIEWABLE_TYPES = {"pdf", "image"}

# Where a compile writes what it produced. Everything in here is READ-ONLY: you
# read a .log, you do not edit it, and the next run overwrites it anyway.
ARTIFACT_DIR = "build"


def _file_row(f: VaultFile, depth: int, parent_id, main_id=None,
              readonly=False) -> dict:
    return {
        "t": "file",
        "id": f.pk,
        "pid": parent_id,
        "depth": depth,
        "name": f.title,
        "file_type": f.file_type,
        "editable": (f.file_type in EDITABLE_TYPES) and not f.is_encrypted,
        "viewable": (f.file_type in VIEWABLE_TYPES) and not f.is_encrypted,
        "encrypted": f.is_encrypted,
        "size": f.file_size_bytes or 0,
        "runnable": f.file_type == "python" and not f.is_encrypted,
        # Generated output. Openable so you can read a failed compile's log,
        # never writable — the next run would overwrite it anyway.
        "readonly": readonly,
        # The one .tex that compiles. The explorer ticks it so nobody has to
        # guess which file the Compile button acts on.
        "main": f.pk == main_id,
    }


def _dir_row(d: VaultDirectory, depth: int, parent_id) -> dict:
    return {
        "t": "dir",
        "id": d.pk,
        "pid": parent_id,
        "depth": depth,
        "name": d.name,
    }


def flatten(workspace) -> list[dict]:
    """The whole bucket, depth-first, as flat rows.

    Three queries regardless of tree size: the directories, then the files, then
    — for a LaTeX workspace — one more to mark which file is the main document.
    The recursion walks dictionaries.

    Files sitting at the bucket root (`directory=None`) are included. They were
    dropped under folder scoping, which is exactly where a shared `preamble.sty`
    tends to live.
    """
    bucket_id = workspace.bucket_id
    if bucket_id is None:
        return []

    # Which file is "main" is the language app's idea (texlab resolves the
    # compiling document; a Python workspace has none) — asked through the
    # registry so this module needs no language imports.
    from . import registry

    app = registry.for_kind(workspace.kind)
    main_id = app.main_id_for(workspace) if app is not None else None

    dirs = list(
        VaultDirectory.objects.filter(bucket_id=bucket_id).order_by("name"))
    files = list(
        VaultFile.objects.filter(bucket_id=bucket_id).order_by("title"))

    children: dict[int | None, list[VaultDirectory]] = {}
    for d in dirs:
        children.setdefault(d.parent_id, []).append(d)

    files_in: dict[int | None, list[VaultFile]] = {}
    for f in files:
        files_in.setdefault(f.directory_id, []).append(f)

    # Which directories are artifact folders, by pk, so a row can be marked
    # read-only without walking its parents again per file.
    artifact_dirs = {d.pk for d in dirs if d.name == ARTIFACT_DIR}

    rows: list[dict] = []

    def visit(parent_id, depth):
        for d in children.get(parent_id, []):
            rows.append(_dir_row(d, depth, parent_id))
            visit(d.pk, depth + 1)
        for f in files_in.get(parent_id, []):
            rows.append(_file_row(f, depth, parent_id, main_id,
                                  readonly=parent_id in artifact_dirs))

    # None is the bucket root: its children sit at depth 0.
    visit(None, 0)
    return rows


def is_artifact(vault_file) -> bool:
    """Whether this file was produced by a compile rather than written by hand.

    The server-side half of the read-only rule. The tree marks these rows so the
    editor can grey them out, but the marking is a courtesy — `file_save` asks
    this, so a hand-crafted POST cannot overwrite a generated log either.
    """
    directory = getattr(vault_file, "directory", None)
    return directory is not None and directory.name == ARTIFACT_DIR
