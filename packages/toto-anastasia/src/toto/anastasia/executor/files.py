"""A capsule's files area: the one place in a capsule that outlives a job.

Everything else the executor holds for a capsule is scratch. ``exec/<id>/in``
and ``/out`` exist for one execution and are removed when it finishes; the
runner's ``/scratch`` is a tmpfs that dies with the container. Until
2026-09-11 that was the whole story, and it meant a person could not put a
file INTO a capsule except as the input of a job, or get one OUT except as
that job's result. ``files/`` is the KEPT area: bound into every runner at
``/files``, read and written one name at a time from Zenobia, and kept for as
long as the reservation — the lifetime rule is written up in ``capsules.py``.

KEPT, NOT DURABLE. The staging root is a sized tmpfs (`deploy.py:
anastasia_staging_mount_unit`), so this area outlives a job and an unmount and
does not outlive a host reboot or a release. The Vault is where a file lives;
this is where a copy of it works. `transfer.py` moves one file either way,
and the desk says so beside the area rather than calling it storage.

THE SAME THREAT MODEL AS ``staging.py``, restated because the two directions
here look less hostile than a tar stream and are not:

* a NAME arrives from the app, composed from a user's Vault or typed by a
  person. It is a path, and every trick a tar member can play, a name can
  play: ``../``, an absolute path, a drive letter, a NUL. ``staging`` already
  judges those, so this module borrows its rule rather than restating it.
* the DIRECTORY is written by runners. A runner may have been compromised by
  the very document it was asked to process — that is what runners are for —
  and it can create a symlink in ``/files`` pointing anywhere on the host.
  The executor runs as root. So a name that IS a symlink, or passes THROUGH
  one, is refused; and the refusal is enforced by how the file is opened, not
  only by a check beforehand. Every step descends with ``O_NOFOLLOW`` through
  a chain of directory descriptors: a check followed by ``open(path)`` is a
  race a runner wins by swapping a directory for a link between the two, and
  "we checked first" would then read ``/etc/shadow`` as root.

What is deliberately NOT here: a search, a rename, a recursive delete, a tar
of the whole area. Each is a bigger surface, and the desk composes what it
needs from the four verbs. ``storage.py`` counts this area like any other and
goes on knowing nothing about names — its tests forbid it from opening a
file, which is why this module exists rather than a widening of that one.

Django-free.
"""

from __future__ import annotations

import errno
import os
import stat
import time
import uuid as uuid_module

from . import staging, storage

#: How many entries one listing returns before saying it stopped, and how
#: long it may walk. A capsule can hold a dependency tree; a desk cannot
#: render one, and a request handler must not sit behind one.
DEFAULT_MAX_ENTRIES = 10_000
DEFAULT_BUDGET_SECONDS = 2.0

_CHUNK = 256 * 1024

#: The voice `staging`'s rule speaks in when it is judging OUR names.
_SUBJECT = "file"
_WHERE = "the files area"

#: Directory descriptors are opened with these and nothing else. `O_NOFOLLOW`
#: makes a symlink fail with ELOOP instead of being walked; `O_DIRECTORY`
#: makes a regular file planted where a directory was fail with ENOTDIR.
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class FilesError(ValueError):
    """A name or a transfer that will not be honoured, and why.

    A ``ValueError`` so the HTTP layer answers it 400 without learning a new
    type: a refused name is the caller's mistake, never the executor's, and a
    500 here would page an operator about somebody's typo.
    """


#: Characters a files-area name may not contain, beyond what `staging` refuses.
#:
#: A BACKSLASH, because `staging.safe_member_name` folds it to a slash — right
#: for a tar written on Windows, wrong here: a runner can create a file
#: literally named ``a\b.txt``, the listing would report it, and reading it
#: back would open ``a/b.txt`` instead. Listing and reading must agree on
#: what a name means or a decoy file can be shown while another's bytes are
#: copied into the user's bucket under its name. (Found 2026-09-11, proven
#: empirically before the fix.)
#:
#: CONTROL CHARACTERS, because a name reaches the vault as a title and a
#: newline in a title makes ``FileResponse`` raise on every download of it;
#: and because a bidi override spoofs an extension in every UI that renders
#: the name. Neither is a file anyone typed.
_REFUSED_CHARS = frozenset("\\") | frozenset(chr(c) for c in range(32)) | {"\x7f"} \
    | frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def safe_name(relative) -> str:
    """The relative POSIX path a caller may name, or raise.

    `staging.safe_member_name` is the rule — normalise, then refuse NUL,
    absolute paths, drive letters and anything that resolves above the root.
    Only the sentence differs. On top of it, the files area refuses what a
    tar member is allowed: see `_REFUSED_CHARS`.
    """
    if not isinstance(relative, str):
        raise FilesError("a file name must be a string")
    try:
        cleaned = staging.safe_member_name(relative, subject=_SUBJECT, where=_WHERE)
    except staging.StagingError as exc:
        raise FilesError(str(exc)) from None
    # Judged on the ORIGINAL, after staging: staging has already folded the
    # backslash away by now, and its NUL sentence is the better one.
    bad = sorted(set(relative) & _REFUSED_CHARS)
    if bad:
        raise FilesError(
            f"{relative[:80]!r} contains a character a file name may not "
            f"({', '.join(repr(c) for c in bad[:3])})")
    return cleaned


def listable(name: str) -> bool:
    """Whether a name a RUNNER wrote may be shown to the desk at all.

    THE RULE: a name is listable only if reading it back would open the same
    file — ``safe_name(name) == name``. Anything else is a name the desk would
    show and could not act on, or worse, one it would act on differently.

    Also refused: a name that is not valid UTF-8 (it arrives from the
    filesystem surrogate-escaped and raises the first time it is rendered or
    written to the database), and a name `safe_name` would normalise — a
    listing must never show ``./x`` beside ``x``.
    """
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    try:
        return safe_name(name) == name
    except FilesError:
        return False


# -- descending safely ------------------------------------------------------

def _open_root(root: str) -> int:
    """A descriptor on the files area itself, or a refusal that says so.

    The root is the manager's own path and is trusted as far as `realpath`;
    what is under it is not. A missing root is a capsule that was never
    mounted or has been released, and creating it here would manufacture a
    capsule directory that `status()` and the sweeper would then have to
    explain.
    """
    if not root or not os.path.isdir(root):
        raise FilesError("this Capsule has no files area; mount it first")
    return os.open(os.path.realpath(root), _DIR_FLAGS & ~os.O_NOFOLLOW)


def _step(dir_fd: int, component: str, name: str, *, create: bool) -> int:
    """Open one directory component under ``dir_fd``, never through a link."""
    try:
        return os.open(component, _DIR_FLAGS, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            # Linux answers a symlink under O_DIRECTORY|O_NOFOLLOW with
            # ENOTDIR, not the ELOOP the manual leads one to expect (measured
            # 2026-09-11, kernel 7.0). The refusal is the same either way;
            # only the sentence needs to know which, so ask — after the fact,
            # where a race can change the word but never the outcome.
            try:
                what = os.stat(component, dir_fd=dir_fd, follow_symlinks=False)
            except OSError:
                what = None
            if what is not None and stat.S_ISLNK(what.st_mode):
                raise FilesError(
                    f"{name!r} passes through a symbolic link, which is refused")
            raise FilesError(
                f"{name!r} passes through something that is not a directory")
        if exc.errno != errno.ENOENT:
            raise FilesError(
                f"the files area could not be opened at {name!r} "
                f"({exc.strerror})")
        if not create:
            raise FilesError(f"there is no file named {name!r} in the files area")
    # Made INSIDE the descriptor chain, so it cannot land anywhere but under
    # a directory we already hold open. `exist_ok` by hand: a runner may have
    # made the same directory between our ENOENT and our mkdir, and that is
    # fine as long as what it made is a directory, which the reopen checks.
    try:
        os.mkdir(component, 0o777, dir_fd=dir_fd)
    except FileExistsError:
        pass
    fd = _step(dir_fd, component, name, create=False)
    # The runner runs as nobody and must be able to write beside a file the
    # app staged next to it — the same reason the area's root is 0777. Set on
    # the open descriptor rather than by path: chmod(path) follows a link.
    # (mkdir's mode is subject to the umask, so this is not redundant.)
    os.fchmod(fd, 0o777)
    return fd


def _descend(root: str, name: str, *, create: bool) -> tuple[list[int], str]:
    """Every descriptor from the root to the parent of ``name``, plus the leaf.

    The caller closes them, last to first. Returned as a list rather than a
    context manager so the caller can keep opening under the deepest one.
    """
    parts = name.split("/")
    fds = [_open_root(root)]
    try:
        for component in parts[:-1]:
            fds.append(_step(fds[-1], component, name, create=create))
    except BaseException:
        _close_all(fds)
        raise
    return fds, parts[-1]


def _close_all(fds) -> None:
    for fd in reversed(fds):
        try:
            os.close(fd)
        except OSError:
            pass


def _budget_phrase(n: int) -> str:
    return staging._budget_phrase(n)


# -- the verbs ---------------------------------------------------------------

def listing(root: str, *, max_entries: int = DEFAULT_MAX_ENTRIES,
            budget_seconds: float = DEFAULT_BUDGET_SECONDS) -> dict:
    """Every file and directory under ``root``, sorted, and whether that is all.

    BY DESCRIPTOR, like every other verb here. The first version walked by
    PATH — ``islink(current)`` and then ``scandir(current)`` — which is the
    exact check-then-open race the module docstring says this module refuses:
    a runner that swaps a directory for a symlink between the two gets the
    host's directory names, sizes and mtimes listed into the desk, as root.
    Each subdirectory is now opened under its parent's descriptor with
    ``O_NOFOLLOW|O_DIRECTORY``, and ``scandir`` is given the descriptor, so a
    link can only ever fail to open.

    Symlinks are neither followed nor listed, and neither is any name that
    `listable` refuses: the desk is only ever shown a name it can hand straight
    back to `read_one` and get the same file.

    ``complete`` is not decoration, for the reason `storage.measure` gives: a
    listing that silently stopped early reads as "this is everything".
    """
    if not root or not os.path.isdir(root):
        return {"files": [], "complete": True}
    try:
        root_fd = _open_root(root)
    except FilesError:
        return {"files": [], "complete": False}

    started = time.monotonic()
    rows: list[dict] = []
    complete = True
    # (fd, relative path of that directory). The root's relative path is "".
    stack: list[tuple[int, str]] = [(root_fd, "")]

    try:
        while stack and complete:
            dir_fd, prefix = stack.pop()
            try:
                # `scandir` on a DESCRIPTOR: entries are relative to it, and
                # nothing on this path is ever a string the kernel resolves
                # from the root. `entry.path` is meaningless here and unused.
                with os.scandir(dir_fd) as entries:
                    for entry in entries:
                        if len(rows) >= max_entries:
                            complete = False
                            break
                        if time.monotonic() - started > budget_seconds:
                            complete = False
                            break
                        if entry.is_symlink():
                            continue
                        relative = f"{prefix}/{entry.name}" if prefix else entry.name
                        if not listable(relative):
                            # A backslash, a control character, non-UTF-8: a
                            # name the desk could not act on, or would act on
                            # wrongly. Not shown — and not counted as
                            # incomplete, because it is not a file anyone can
                            # move.
                            continue
                        try:
                            info = entry.stat(follow_symlinks=False)
                        except OSError:
                            # Vanished mid-walk. Normal in a live capsule.
                            continue
                        if stat.S_ISDIR(info.st_mode):
                            rows.append({"name": relative, "size": 0,
                                         "modified": int(info.st_mtime),
                                         "is_dir": True})
                            try:
                                child = os.open(entry.name, _DIR_FLAGS,
                                                dir_fd=dir_fd)
                            except OSError:
                                # Became a link, or vanished, since `stat`.
                                # Listed as a directory — that is what it
                                # was — and not descended.
                                continue
                            stack.append((child, relative))
                        elif stat.S_ISREG(info.st_mode):
                            rows.append({"name": relative, "size": info.st_size,
                                         "modified": int(info.st_mtime),
                                         "is_dir": False})
                        # A socket, a FIFO, a device: not a file anyone can
                        # move, and not named — the desk would only offer to
                        # read it.
            except OSError:
                # An unreadable directory is a fact about permissions, not a
                # reason to report the rest of the tree as absent.
                complete = False
            finally:
                if dir_fd != root_fd:
                    os.close(dir_fd)
    finally:
        for fd, _ in stack:
            if fd != root_fd:
                os.close(fd)
        os.close(root_fd)

    rows.sort(key=lambda row: row["name"])
    return {"files": rows, "complete": complete}


def read_one(root: str, relative, *, max_bytes: int) -> bytes:
    """The bytes of one regular file, bounded twice.

    The size is checked from ``fstat`` BEFORE any byte is read, so a file over
    budget costs a refusal rather than a copy of it. And the read is capped as
    well, because the size was read before the bytes and a runner can append
    between the two — the budget has to bind where the work actually happens.
    """
    name = safe_name(relative)
    fds, leaf = _descend(root, name, create=False)
    try:
        try:
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                         dir_fd=fds[-1])
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise FilesError(
                    f"{name!r} is a symbolic link, which is refused") from None
            if exc.errno == errno.ENOENT:
                raise FilesError(
                    f"there is no file named {name!r} in the files area") from None
            raise FilesError(f"{name!r} could not be opened ({exc.strerror})") from None
        fds.append(fd)
        info = os.fstat(fd)
        if stat.S_ISDIR(info.st_mode):
            raise FilesError(f"{name!r} is a directory, not a file")
        if not stat.S_ISREG(info.st_mode):
            raise FilesError(f"{name!r} is not a regular file")
        if info.st_size > max_bytes:
            raise FilesError(
                f"{name!r} is {_budget_phrase(info.st_size)}, over the "
                f"{_budget_phrase(max_bytes)} transfer budget")
        out = bytearray()
        while True:
            chunk = os.read(fd, _CHUNK)
            if not chunk:
                break
            out += chunk
            if len(out) > max_bytes:
                raise FilesError(
                    f"{name!r} grew past the {_budget_phrase(max_bytes)} "
                    "transfer budget while it was being read")
        return bytes(out)
    finally:
        _close_all(fds)


def area_bytes(root: str) -> tuple[int, bool]:
    """How much the files area holds, and whether that number is the whole.

    `storage.measure`'s walk, so the two numbers the desk can see — the
    storage tile and the refusal — never disagree. Incomplete means the area
    has more entries than the walk's ceiling or took longer than its time
    budget, and either is itself a sign the area is out of hand.
    """
    if not root or not os.path.isdir(root):
        return 0, True
    measured = storage.measure(root)
    return measured["bytes"], measured["complete"]


def check_area_budget(root: str, adding: int, max_area_bytes: int | None,
                      *, who: str = "the files area") -> None:
    """Refuse when ``adding`` bytes would take the area past its budget.

    THE ONE TOTAL BOUND THE AREA HAS. Each file is bounded by `max_bytes`,
    each runner's writes by its cgroup, but the area outlives both an
    execution and an unmount, so nothing bounded the SUM — and the staging
    root is a tmpfs shared by every capsule on the host. A soft check: the
    measurement is a walk and a runner can write between it and the put.
    That is fine for a budget; it would not be fine for a security boundary,
    and this is not one.

    An area too large to measure is treated as over budget rather than
    unknowable, because "unknowable" would be the one answer that lets it
    grow further.
    """
    if max_area_bytes is None:
        return
    held, complete = area_bytes(root)
    if not complete:
        raise FilesError(
            f"{who} holds more than can be measured; delete something "
            "before adding to it")
    if held + adding > max_area_bytes:
        raise FilesError(
            f"{who} holds {_budget_phrase(held)} and this would add "
            f"{_budget_phrase(adding)}, over the "
            f"{_budget_phrase(max_area_bytes)} area budget")


def write_one(root: str, relative, data, *, max_bytes: int,
              replace: bool = False, max_area_bytes: int | None = None) -> dict:
    """Put one file in place, atomically, and only where the name says.

    Written to a temporary name in the SAME directory and moved into place in
    one step, so a reader — a runner, or a concurrent `read_one` — sees the
    old file or the new one and never a half-written one. Without `replace`
    the move is a ``link``, which the kernel refuses if the name exists: a
    check-then-rename would clobber a file a runner created in between.

    Mode 0644 for the same reason `staging.unpack` forces it: what the app
    hands over must never arrive executable or setuid in a directory a runner
    can reach.
    """
    name = safe_name(relative)
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise FilesError("file content must be bytes")
    size = len(data)
    if size > max_bytes:
        raise FilesError(
            f"{name!r} is {_budget_phrase(size)}, over the "
            f"{_budget_phrase(max_bytes)} transfer budget")
    # A replace frees what it replaces, but counting on that would need a
    # second stat and the budget is soft anyway: the simpler rule is that
    # the area must have room for the whole file beside what it holds.
    check_area_budget(root, size, max_area_bytes)

    fds, leaf = _descend(root, name, create=True)
    parent = fds[-1]
    tmp = f".{leaf}.{uuid_module.uuid4().hex[:12]}.part"
    try:
        # Judged with lstat, so a symlink is never "a file we may replace".
        try:
            existing = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if stat.S_ISDIR(existing.st_mode):
                raise FilesError(f"{name!r} is a directory")
            if not stat.S_ISREG(existing.st_mode):
                raise FilesError(
                    f"{name!r} exists and is not a regular file, so it will "
                    "not be replaced")
            if not replace:
                raise FilesError(
                    f"{name!r} already exists; ask to replace it if that is "
                    "what you mean")

        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644, dir_fd=parent)
            fds.append(fd)
            view = memoryview(data)
            while len(view):
                written = os.write(fd, view[:_CHUNK])
                view = view[written:]
            os.fchmod(fd, 0o644)
            os.fsync(fd)
            if replace:
                os.replace(tmp, leaf, src_dir_fd=parent, dst_dir_fd=parent)
            else:
                try:
                    os.link(tmp, leaf, src_dir_fd=parent, dst_dir_fd=parent)
                except FileExistsError:
                    raise FilesError(
                        f"{name!r} already exists; ask to replace it if that "
                        "is what you mean") from None
        except OSError as exc:
            raise FilesError(
                f"{name!r} could not be written ({exc.strerror})") from None
        return {"name": name, "size": size}
    finally:
        # Whatever happened, the temporary name does not outlive the call:
        # after a `replace` it is already gone, after a `link` it is a second
        # name for the same bytes, and after a failure it is the half-written
        # file nobody must find. Never by path — `parent` is still open.
        try:
            os.unlink(tmp, dir_fd=parent)
        except OSError:
            pass
        _close_all(fds)


def delete_one(root: str, relative) -> dict:
    """Remove one regular file. Never a directory, never a link.

    A link is refused rather than unlinked even though unlinking one is
    harmless, because "delete refuses what read refuses" is a rule a reader
    can hold in their head, and the exception would be the one they forget.
    """
    name = safe_name(relative)
    fds, leaf = _descend(root, name, create=False)
    try:
        try:
            info = os.stat(leaf, dir_fd=fds[-1], follow_symlinks=False)
        except FileNotFoundError:
            raise FilesError(
                f"there is no file named {name!r} in the files area") from None
        if stat.S_ISDIR(info.st_mode):
            raise FilesError(f"{name!r} is a directory; only files are deleted")
        if not stat.S_ISREG(info.st_mode):
            raise FilesError(f"{name!r} is not a regular file")
        try:
            os.unlink(leaf, dir_fd=fds[-1])
        except FileNotFoundError:
            pass                       # a runner beat us to it; the outcome holds
        except OSError as exc:
            raise FilesError(
                f"{name!r} could not be deleted ({exc.strerror})") from None
        return {"name": name, "deleted": True}
    finally:
        _close_all(fds)
