"""The Vault as a pane beside a Capsule, for the two-way transfer window.

The Files tab's transfer window puts the person's Vault on one side and the
Capsule's files area on the other, and copies either way, one file per
request. This module builds the VAULT side's rows. The Capsule side's rows are
`views._vault_rows`, and the two share one shape on purpose: a pre-ordered,
depth-first list where every row carries its depth and its parent row's id,
each folder followed by its subfolders and then its own files — the shape the
Vault's own browser walks.

WHY NOT THE VAULT'S OWN BUILDERS. `vault/views.py` has three, and each is
shaped for its page: `_build_flat_items` mixes every bucket's top level at
depth 0 and silently drops a file whose folder was not passed in;
`_build_source_tree` is one bucket; `build_file_tree` is grouped by a path
string, merges two folders of one name, and cannot say which folders are
empty. And toto-base is pull-only. So this is a portal-owned builder, pinned
by `tests/test_transfer_window.py`.

WHAT A ROW PROMISES, because the window acts on it:

* a FILE row is one of the person's own files (`owner=user`) — exactly the
  scope the copy-in door accepts, so the pane never offers a file the door
  refuses as someone else's. A file that cannot be copied still appears, with
  `copyable: false` and `why`, rather than vanishing: an encrypted file would
  reach the Capsule as ciphertext, and a file in a remote bucket has no bytes
  on this server to copy.
* a BUCKET or FOLDER row is a DESTINATION only when `writable`: a local bucket
  the person owns, and a folder in it they own — the rule the copy-out door
  enforces (`resolve_owned_directory`).
* ids come from ONE counter, so a folder and a file never share one, and the
  real primary keys travel in their own fields (`bpk`, `dpk`, `fpk`). Ids are
  positional and change on refresh; the window keys everything it keeps —
  what is open, what is ticked, where it is copying to — on the real keys.
"""

from __future__ import annotations

import itertools

#: How many Vault files the pane draws. Past this the pane says it stopped
#: rather than drawing a prefix that reads as the whole Vault.
VAULT_PANE_LIMIT = 2000

#: Why a file row cannot be copied into a Capsule. Codes; the page words them.
ENCRYPTED = "encrypted"
REMOTE = "remote"


#: The largest key a 64-bit primary key column holds. The vault's own resolver
#: bounds ids by it because SQLite raises OverflowError for a larger integer
#: instead of finding nothing.
MAX_PK = 2 ** 63 - 1


def parse_pk(raw) -> int | None:
    """A primary key from a form value, or None — never an exception.

    Not `str.isdigit`: it is true for "²", which `int` refuses, and the door
    that trusted it answered a 500 instead of "no such file".
    """
    try:
        pk = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return pk if 0 < pk <= MAX_PK else None


def resolve_owned_directory(user, directory_id, bucket):
    """A folder the person owns, in that bucket — or None.

    The rule `vault/api_views.py:_resolve_owned_directory` applies to an
    upload's target folder, restated because toto-base is pull-only: junk and
    out-of-range ids are an absence, not an error, and a folder in another
    bucket or owned by somebody else is the same absence — never "that folder
    exists but is not yours".
    """
    from toto.vault.models import VaultDirectory

    pk = parse_pk(directory_id)
    if pk is None:
        return None
    return VaultDirectory.objects.filter(pk=pk, bucket=bucket, owner=user).first()


def _local(bucket) -> bool:
    """Whether a bucket's bytes are on this server — `access.is_local_content`'s
    rule, where a file with no bucket at all is local."""
    return bucket is None or bool(bucket.is_local)


def _file_row(f, *, row_id: int, pid, depth: int) -> dict:
    why = ENCRYPTED if f.is_encrypted else ("" if _local(f.bucket) else REMOTE)
    return {
        "t": "file", "id": row_id, "pid": pid, "depth": depth,
        "fpk": f.pk, "bpk": f.bucket_id,
        "title": f.title, "key": f.key,
        "size": int(f.file_size_bytes or 0),
        "file_type": f.file_type,
        "copyable": not why, "why": why,
    }


def vault_pane_rows(user, *, limit: int = VAULT_PANE_LIMIT) -> dict:
    """The person's Vault as depth-first rows: buckets, folders, files.

    Returns ``{"items": [...], "truncated": bool, "limit": int}``.

    Which buckets: every bucket the person owns (destinations, even empty) and
    every bucket holding one of their files (sources). Which folders: every
    folder they own in a bucket they own, and every ANCESTOR of a listed file
    or folder — a file in a folder somebody else made must still hang from
    that folder, not disappear because the folder is not theirs. A file whose
    folder is missing, or is in another bucket, hangs from its bucket.
    """
    from toto.vault.models import Bucket, VaultDirectory, VaultFile

    files = list(VaultFile.objects.filter(owner=user)
                 .select_related("bucket")
                 .order_by("bucket__name", "title", "pk")[:limit + 1])
    truncated = len(files) > limit
    files = files[:limit]

    owned = {b.pk: b for b in Bucket.objects.filter(owner=user)}
    buckets = dict(owned)
    for f in files:
        if f.bucket_id and f.bucket_id not in buckets:
            buckets[f.bucket_id] = f.bucket

    dirs = {d.pk: d for d in VaultDirectory.objects
            .filter(bucket_id__in=list(buckets))
            .only("pk", "name", "parent_id", "bucket_id", "owner_id")}

    shown: set = set()

    def show_with_ancestors(pk) -> None:
        # Bounded, because a parent chain is data and data can be a cycle.
        for _step in range(len(dirs) + 1):
            if pk is None or pk not in dirs or pk in shown:
                return
            shown.add(pk)
            pk = dirs[pk].parent_id

    for d in dirs.values():
        if d.owner_id == user.pk and d.bucket_id in owned:
            show_with_ancestors(d.pk)

    files_under: dict = {}
    for f in files:
        folder = dirs.get(f.directory_id)
        dpk = folder.pk if folder is not None and folder.bucket_id == f.bucket_id else None
        if dpk is not None:
            show_with_ancestors(dpk)
        files_under.setdefault((f.bucket_id, dpk), []).append(f)

    children: dict = {}
    for pk in shown:
        d = dirs[pk]
        parent = d.parent_id if d.parent_id in shown else None
        children.setdefault((d.bucket_id, parent), []).append(d)

    paths: dict = {}

    def path_of(pk) -> str:
        if pk in paths:
            return paths[pk]
        names, cursor = [], pk
        for _step in range(len(dirs) + 1):
            if cursor is None or cursor not in shown:
                break
            names.append(dirs[cursor].name)
            cursor = dirs[cursor].parent_id
        paths[pk] = "/".join(reversed(names))
        return paths[pk]

    counter = itertools.count(1)
    rows: list = []

    def by_name(d):
        return (d.name.lower(), d.pk)

    def by_title(f):
        return ((f.title or "").lower(), f.pk)

    def emit_folders(bpk, parent_pk, depth, pid) -> None:
        for d in sorted(children.get((bpk, parent_pk), ()), key=by_name):
            row_id = next(counter)
            under = files_under.get((bpk, d.pk), ())
            rows.append({
                "t": "dir", "id": row_id, "pid": pid, "depth": depth,
                "bpk": bpk, "dpk": d.pk, "name": d.name, "path": path_of(d.pk),
                "n_dirs": len(children.get((bpk, d.pk), ())),
                "n_files": len(under),
                "writable": (d.owner_id == user.pk and bpk in owned
                             and _local(buckets.get(bpk))),
            })
            emit_folders(bpk, d.pk, depth + 1, row_id)
            for f in sorted(under, key=by_title):
                rows.append(_file_row(f, row_id=next(counter), pid=row_id,
                                      depth=depth + 1))

    for bucket in sorted(buckets.values(), key=lambda b: (b.name.lower(), b.pk)):
        row_id = next(counter)
        root_files = files_under.get((bucket.pk, None), ())
        rows.append({
            "t": "bucket", "id": row_id, "pid": None, "depth": 0,
            "bpk": bucket.pk, "slug": bucket.slug, "name": bucket.name,
            "local": _local(bucket),
            "writable": bucket.pk in owned and _local(bucket),
            "n_dirs": len(children.get((bucket.pk, None), ())),
            "n_files": len(root_files),
        })
        emit_folders(bucket.pk, None, 1, row_id)
        for f in sorted(root_files, key=by_title):
            rows.append(_file_row(f, row_id=next(counter), pid=row_id, depth=1))

    loose = files_under.get((None, None), ())
    if loose:
        row_id = next(counter)
        # Files with no bucket at all: listed under a row of their own, never
        # a destination — a copy out has to name a bucket.
        rows.append({"t": "bucket", "id": row_id, "pid": None, "depth": 0,
                     "bpk": None, "slug": "", "name": "", "local": True,
                     "writable": False, "n_dirs": 0, "n_files": len(loose)})
        for f in sorted(loose, key=by_title):
            rows.append(_file_row(f, row_id=next(counter), pid=row_id, depth=1))

    return {"items": rows, "truncated": truncated, "limit": limit}
