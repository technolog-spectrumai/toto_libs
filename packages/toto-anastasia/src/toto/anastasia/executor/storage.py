"""How much a capsule is occupying. COUNTS ONLY, NEVER CONTENTS.

An operator needs to answer "what is using the disk" without being able to
answer "what is in there". A capsule holds somebody's work, and the platform's
position is that its contents are not the operator's business — so this module
returns integers and nothing else.

THE SEAM IS THE POINT, and it is deliberately narrow enough to review at a
glance: every function here returns a dict of numbers. There is no parameter
that widens it into a listing, no filename crosses the boundary, and nothing
opens a file. `du` is an accounting question; `ls` is a privacy one, and this
module answers only the first.

Django-free, like the rest of the executor.
"""

from __future__ import annotations

import os
import time

#: Give up rather than walk for ever. A capsule with a pathological tree —
#: a dependency directory with a hundred thousand files is ordinary, not
#: pathological — must not hold the reconcile loop open.
DEFAULT_MAX_ENTRIES = 200_000

#: And a wall-clock ceiling, because entry count is a poor proxy for cost on a
#: cold cache or a slow disk.
DEFAULT_BUDGET_SECONDS = 5.0


def measure(path: str, *, max_entries: int = DEFAULT_MAX_ENTRIES,
            budget_seconds: float = DEFAULT_BUDGET_SECONDS) -> dict:
    """Bytes and file counts under ``path``, plus whether we saw all of it.

    ``complete`` is not decoration. A number that silently stopped early reads
    as "this capsule is small" — the opposite of the truth, and exactly the
    reading an operator would act on. A partial answer says so.

    Uses ``lstat`` and never follows a symlink: a link into the host would
    otherwise be counted as the capsule's own, and following one is a read.
    Apparent size, not blocks, because the question is "how much did this
    capsule put here", which sparse files and compression would distort.
    """
    started = time.monotonic()
    total_bytes = 0
    files = 0
    directories = 0
    links = 0
    complete = True

    if not path or not os.path.isdir(path):
        return _result(0, 0, 0, 0, True, 0.0)

    stack = [path]
    while stack:
        if files + directories >= max_entries:
            complete = False
            break
        if time.monotonic() - started > budget_seconds:
            complete = False
            break
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    # CHECK INSIDE THE LOOP, not only between directories.
                    # The first version tested the budget once per directory,
                    # so a single directory holding a million files — which a
                    # dependency tree easily is — was walked to the end no
                    # matter what the caller asked for. The ceiling has to bind
                    # where the work actually happens.
                    if files + directories >= max_entries:
                        complete = False
                        break
                    if time.monotonic() - started > budget_seconds:
                        complete = False
                        break
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        # A file that vanished mid-walk is normal in a live
                        # capsule and is not worth failing the whole reading.
                        continue
                    if entry.is_symlink():
                        links += 1
                    elif entry.is_dir(follow_symlinks=False):
                        directories += 1
                        stack.append(entry.path)
                    else:
                        files += 1
                        total_bytes += info.st_size
        except OSError:
            # An unreadable directory is a fact about permissions, not a
            # reason to report zero for everything above it.
            complete = False
            continue
        if not complete:
            break

    return _result(total_bytes, files, directories, links, complete,
                   time.monotonic() - started)


def _result(total_bytes, files, directories, links, complete, seconds) -> dict:
    return {
        "bytes": total_bytes,
        "files": files,
        "directories": directories,
        "symlinks": links,
        "complete": complete,
        "measured_in_seconds": round(seconds, 3),
    }


def by_area(root: str, areas, **kwargs) -> dict:
    """The same numbers per named subdirectory, so growth can be attributed.

    Names come from the CALLER, never from the filesystem — that is what keeps
    this from becoming a listing by another route. A caller asks about "in",
    "out" and "scratch" because it already knows those exist; it cannot ask
    "what directories are there".
    """
    out = {}
    for area in areas:
        out[area] = measure(os.path.join(root, area), **kwargs)
    return out
