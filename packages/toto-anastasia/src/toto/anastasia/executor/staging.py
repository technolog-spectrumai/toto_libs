"""Getting bytes in and out of a runner, without trusting either direction.

Inputs arrive from a caller as a tar stream and are unpacked into a per-lease
staging directory. Outputs come back the same way. **Both** directions are
untrusted: the input because a caller composes it from a user's Vault, the
output because a runner may have been compromised by the very document it was
asked to process — that is what the runner is *for*.

So the same rules apply either way:

* every member must be a regular file or a directory; no symlinks, no hard
  links, no devices, no FIFOs. A symlink is how a tar escapes: ``ln -s /etc``
  followed by ``etc/passwd`` writes outside the tree even though neither member
  name contains "..".
* every path is re-checked after joining, not merely inspected before. Prefix
  checks on the *name* miss absolute paths, drive letters and unicode tricks;
  a check on the *resolved* path cannot.
* a byte budget is enforced while unpacking, so a decompression bomb fails at
  the budget rather than at the disk.
* the member count is capped, because a million empty files is also a bomb.

Python's ``tarfile.extractall(filter="data")`` does much of this in 3.12+, but
not the budget and not the count — and this code has to run on 3.10. So the
loop is explicit.

Django-free.
"""

from __future__ import annotations

import io
import os
import tarfile

#: Refuse a member whose name is not a plain relative path. Mirrors
#: ``families._SAFE_PATH``; kept separate because that one describes what a
#: CALLER may name and this one describes what an ARCHIVE may contain.
MAX_NAME_LENGTH = 255
MAX_MEMBERS = 10_000


def _budget_phrase(max_bytes: int) -> str:
    """Say the budget in a unit that is not zero.

    "exceeds the 0 MB budget" is what integer-dividing a small budget by a
    megabyte produces, and it reads as a bug rather than as a limit.
    """
    if max_bytes >= 1024 * 1024:
        return f"{max_bytes // (1024 * 1024)} MB"
    if max_bytes >= 1024:
        return f"{max_bytes // 1024} KB"
    return f"{max_bytes} bytes"


class StagingError(Exception):
    """An archive that will not be unpacked, and why."""


def safe_member_name(name: str, *, subject: str = "archive member",
                     where: str = "the staging directory") -> str:
    """The relative path this member may occupy, or raise.

    Normalises first and rejects afterwards: ``a/./../../b`` only reveals
    itself as an escape once normalised.

    ``subject`` and ``where`` only change the SENTENCE. Since 2026-09-11 the
    same rule judges a name the app sends to a capsule's files area (see
    ``files.py``), and "archive member escapes the staging directory" is a
    baffling thing to tell somebody who typed a file name into a desk. One
    rule with two voices, rather than two rules that can disagree.
    """
    if not name or len(name) > MAX_NAME_LENGTH:
        raise StagingError(f"{subject} has an unusable name: {name[:80]!r}")
    if "\x00" in name:
        # Unreachable from a real tar — the format NUL-terminates names, so a
        # member written as "a\0b" arrives as "a". Kept because this function is
        # the one place a path is judged, and it should not depend on WHERE the
        # name came from to be correct — and a JSON body, unlike a tar, CAN
        # carry one.
        raise StagingError(f"{subject} name contains a NUL byte")

    cleaned = name.replace("\\", "/")
    if cleaned.startswith("/") or (len(cleaned) > 1 and cleaned[1] == ":"):
        raise StagingError(f"{subject} is an absolute path: {name!r}")

    normalised = os.path.normpath(cleaned)
    if normalised in (".", "..") or normalised.startswith("../"):
        raise StagingError(f"{subject} escapes {where}: {name!r}")
    return normalised


def resolved_within(root: str, relative: str, *,
                    subject: str = "archive member",
                    where: str = "staging") -> str:
    """Join and prove containment on the RESOLVED path.

    ``os.path.realpath`` rather than ``abspath``: if an earlier member managed
    to create a symlink, abspath would happily report a contained path that
    resolves outside. We refuse symlink members entirely, so this is
    defence in depth — which is exactly where it belongs.

    Public since 2026-09-11 because ``files.py`` proves the same containment
    over a directory runners write to. A second copy of a containment check is
    a second place for ``startswith(root)`` to forget the separator and let
    ``/staging-evil`` pass as inside ``/staging``.
    """
    root_real = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root_real, relative))
    if target != root_real and not target.startswith(root_real + os.sep):
        raise StagingError(f"{subject} would land outside {where}: {relative!r}")
    return target


#: The pre-promotion name. Kept so nothing that imported it breaks; new code
#: uses the public one.
_resolved_within = resolved_within


def unpack(data: bytes, destination: str, *, max_bytes: int,
           max_members: int = MAX_MEMBERS) -> dict:
    """Unpack a tar into ``destination``. Returns what was written.

    Raises :class:`StagingError` on anything suspicious, having written
    whatever it wrote so far — the caller destroys the staging directory on
    failure, so a partial unpack is not a state anyone has to reason about.
    """
    os.makedirs(destination, exist_ok=True)
    written = 0
    files = 0

    try:
        archive = tarfile.open(fileobj=io.BytesIO(data or b""), mode="r:*")
    except tarfile.TarError as exc:
        raise StagingError(f"input is not a readable tar archive: {exc}") from None

    with archive:
        for index, member in enumerate(archive):
            if index >= max_members:
                raise StagingError(
                    f"archive holds more than {max_members} members")

            if member.issym() or member.islnk():
                raise StagingError(
                    f"archive contains a link ({member.name!r}); links are how "
                    "an archive escapes its directory")
            if member.ischr() or member.isblk() or member.isfifo():
                raise StagingError(
                    f"archive contains a device or FIFO ({member.name!r})")
            if not (member.isfile() or member.isdir()):
                raise StagingError(
                    f"archive member {member.name!r} is not a file or directory")

            relative = safe_member_name(member.name)
            target = resolved_within(destination, relative)

            if member.isdir():
                os.makedirs(target, exist_ok=True)
                continue

            if written + member.size > max_bytes:
                raise StagingError(
                    f"staged input exceeds the {_budget_phrase(max_bytes)} "
                    "budget for this job")

            os.makedirs(os.path.dirname(target) or destination, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:               # pragma: no cover - isfile() said otherwise
                continue
            # Copy in bounded chunks and count as we go, so a member whose
            # header lies about its size still cannot exceed the budget.
            with open(target, "wb") as handle:
                remaining = max_bytes - written
                while True:
                    chunk = source.read(256 * 1024)
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise StagingError(
                            "staged input exceeds its budget (the archive "
                            "header understated a member's size)")
                    handle.write(chunk)
                    written += len(chunk)
            # Never honour stored modes: 4755 from an archive is a setuid
            # binary in the runner's staging directory.
            os.chmod(target, 0o644)
            files += 1

    return {"files": files, "bytes": written}


def pack(source: str, *, max_bytes: int, max_members: int = MAX_MEMBERS) -> bytes:
    """Tar a directory up for return to the caller, bounded the same way.

    Symlinks a runner may have created are SKIPPED rather than followed: a
    runner that leaves ``output.pdf -> /etc/shadow`` in /out must not be able
    to exfiltrate through the result channel.
    """
    buffer = io.BytesIO()
    written = 0
    members = 0
    root = os.path.realpath(source)

    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for directory, _subdirs, filenames in os.walk(root, followlinks=False):
            for filename in sorted(filenames):
                path = os.path.join(directory, filename)
                if os.path.islink(path) or not os.path.isfile(path):
                    continue
                size = os.path.getsize(path)
                if written + size > max_bytes:
                    raise StagingError(
                        "the job produced more output than the "
                        f"{_budget_phrase(max_bytes)} return budget allows")
                members += 1
                if members > max_members:
                    raise StagingError("the job produced too many output files")
                info = tarfile.TarInfo(os.path.relpath(path, root))
                info.size = size
                info.mode = 0o644
                info.mtime = 0          # reproducible; nothing depends on it
                with open(path, "rb") as handle:
                    archive.addfile(info, handle)
                written += size

    return buffer.getvalue()
