"""A capsule's files area: the one place in a capsule that outlives a job.

Everything else the executor holds for a capsule is scratch. ``exec/<id>/in``
and ``/out`` exist for one execution and are removed when it finishes; the
runner's ``/scratch`` is a tmpfs that dies with the container. Until
2026-09-11 that was the whole story, and it meant a person could not put a
file INTO a capsule except as the input of a job, or get one OUT except as
that job's result. ``files/`` is the durable area: bound into every runner at
``/files``, read and written one name at a time from Zenobia, and kept for as
long as the reservation — the lifetime rule is written up in ``capsules.py``.

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

from . import staging

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


def safe_name(relative) -> str:
    """The relative POSIX path a caller may name, or raise.

    `staging.safe_member_name` is the rule — normalise, then refuse NUL,
    absolute paths, drive letters and anything that resolves above the root.
    Only the sentence differs.
    """
    if not isinstance(relative, str):
        raise FilesError("a file name must be a string")
    try:
        return staging.safe_member_name(relative, subject=_SUBJECT, where=_WHERE)
    except staging.StagingError as exc:
        raise FilesError(str(exc)) from None


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

    Symlinks are neither followed nor listed. A runner's link is something
    `read_one` refuses and `write_one` will not write through, so listing it
    would offer the desk a name it can do nothing with — and following it
    would let a link to ``/`` list the host.

    ``complete`` is not decoration, for the reason `storage.measure` gives: a
    listing that silently stopped early reads as "this is everything".
    """
    if not root or not os.path.isdir(root):
        return {"files": [], "complete": True}
    root_real = os.path.realpath(root)
    started = time.monotonic()
    rows: list[dict] = []
    complete = True
    stack = [root_real]

    while stack and complete:
        current = stack.pop()
        # RE-PROVEN PER DIRECTORY, not once at the top. A directory pushed as
        # a real directory may be a symlink by the time it is popped — a
        # runner is writing this tree while we read it — and `scandir` on a
        # path follows it.
        if os.path.islink(current):
            continue
        try:
            staging.resolved_within(root_real, os.path.relpath(current, root_real),
                                    subject=_SUBJECT, where=_WHERE)
        except staging.StagingError:
            continue
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if len(rows) >= max_entries:
                        complete = False
                        break
                    if time.monotonic() - started > budget_seconds:
                        complete = False
                        break
                    if entry.is_symlink():
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        # Vanished mid-walk. Normal in a live capsule.
                        continue
                    relative = os.path.relpath(entry.path, root_real)
                    relative = relative.replace(os.sep, "/")
                    if stat.S_ISDIR(info.st_mode):
                        rows.append({"name": relative, "size": 0,
                                     "modified": int(info.st_mtime),
                                     "is_dir": True})
                        stack.append(entry.path)
                    elif stat.S_ISREG(info.st_mode):
                        rows.append({"name": relative, "size": info.st_size,
                                     "modified": int(info.st_mtime),
                                     "is_dir": False})
                    # A socket, a FIFO, a device: not a file anyone can move,
                    # and not named — the desk would only offer to read it.
        except OSError:
            # An unreadable directory is a fact about permissions, not a
            # reason to report the rest of the tree as absent.
            complete = False

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


def write_one(root: str, relative, data, *, max_bytes: int,
              replace: bool = False) -> dict:
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
