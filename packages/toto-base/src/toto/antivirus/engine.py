"""The thing `toto.vault.scanning` calls once the app is installed.

Everything here assumes antivirus IS present — the "is it installed" question
was answered by the façade before it got this far. Keeping that check in one
place is the point: every consumer says `scanning.scan(...)`, and only this
module knows there is a registry behind it.
"""

from __future__ import annotations

import hashlib

from toto.vault.scanning import Verdict, is_scannable

from .scanners import scanner_for


def _as_text(data) -> str | None:
    """Bytes or str to text, or None if it is not text at all."""
    if isinstance(data, str):
        return data
    if isinstance(data, (bytes, bytearray, memoryview)):
        try:
            return bytes(data).decode("utf-8")
        except UnicodeDecodeError:
            return None
    return None


def scan(data, *, file_type: str, filename: str = "") -> Verdict:
    """Screen content. Returns clean-and-unscanned for anything we cannot read."""
    from .scanners import is_binary

    scanner = scanner_for(file_type)
    if scanner is None:
        return Verdict.clean(scanned=False)

    # A binary scanner takes the raw bytes. PDF is the reason this branch
    # exists: a real PDF does not decode as UTF-8, and pushing it through the
    # text path refused every one as "wrong-shape" without ever looking at it.
    if is_binary(file_type):
        raw = data.encode("utf-8") if isinstance(data, str) else bytes(data or b"")
        return scanner(raw)

    text = _as_text(data)
    if text is None:
        # A file claiming to be XML that is not even UTF-8 is not something to
        # wave through, but it is also not a threat we can name. Refuse it as
        # the wrong shape rather than pretending to have understood it.
        return Verdict.refused("wrong-shape", "not valid UTF-8 text")
    return scanner(text)


def digest(data) -> str:
    payload = data.encode("utf-8") if isinstance(data, str) else bytes(data or b"")
    return hashlib.sha256(payload).hexdigest()


def record(vault_file, verdict: Verdict, *, user=None, door: str = "",
           content=None) -> None:
    """Remember a verdict against the bytes it was about.

    The hash comes from the content we actually looked at when we have it, and
    falls back to the file's stored hash. That distinction matters at the editor
    door, which historically did not update `content_hash` at all.
    """
    from .models import ScanResult, ScanVerdict

    content_hash = digest(content) if content is not None else (
        vault_file.content_hash or "")
    if not content_hash:
        return None

    if isinstance(content, str):
        size = len(content.encode("utf-8"))
    elif content is not None:
        size = len(bytes(content))
    else:
        size = vault_file.file_size_bytes or 0

    row, _created = ScanResult.objects.update_or_create(
        file=vault_file,
        content_sha256=content_hash,
        defaults={
            "file_type": vault_file.file_type,
            "verdict": ScanVerdict.CLEAN if verdict.ok else ScanVerdict.REFUSED,
            "reason": verdict.reason,
            "detail": verdict.detail[:2000],
            "line": verdict.line,
            "door": door,
            "size_bytes": size,
            "scanned_by": user if getattr(user, "is_authenticated", False) else None,
        },
    )
    return row


def record_failure(vault_file, detail: str, *, user=None, door: str = "") -> None:
    """Remember that a scan could not run at all.

    Keyed on the file's stored hash (there is no content — that is the point),
    so a later successful scan of the same bytes replaces this row rather than
    sitting beside it.
    """
    from .models import ScanResult, ScanVerdict

    row, _created = ScanResult.objects.update_or_create(
        file=vault_file,
        content_sha256=vault_file.content_hash or "unreadable",
        defaults={
            "file_type": vault_file.file_type,
            "verdict": ScanVerdict.ERROR,
            "reason": "unreadable",
            "detail": detail[:2000],
            "line": 0,
            "door": door,
            "size_bytes": 0,
            "scanned_by": user if getattr(user, "is_authenticated", False) else None,
        },
    )


def clean_file_ids(files) -> set[int]:
    """Ids among `files` whose CURRENT bytes have a clean verdict.

    One query for the whole listing. A file whose content changed since its scan
    has no row for its present hash and therefore, correctly, no tick.
    """
    from .models import ScanResult, ScanVerdict

    wanted = {}
    for vault_file in files:
        content_hash = getattr(vault_file, "content_hash", "")
        if content_hash:
            wanted[(vault_file.pk, content_hash)] = vault_file.pk
    if not wanted:
        return set()

    rows = ScanResult.objects.filter(
        file_id__in={pk for pk, _ in wanted},
        verdict=ScanVerdict.CLEAN,
    ).values_list("file_id", "content_sha256")
    return {pk for pk, content_hash in rows if (pk, content_hash) in wanted}


#: What a health report's overall status can be. Derived, never stored.
STATUS_THREATS = "threats"      # something hostile is in the bucket NOW
STATUS_ATTENTION = "attention"  # a file could not be scanned
STATUS_PARTIAL = "partial"      # scannable files nobody has scanned yet
STATUS_CLEAN = "clean"          # everything scannable scanned, all clean
STATUS_NONE = "none"            # nothing here a scanner can read


def health_report(files) -> dict:
    """Counts and a verdict over a set of vault files, for the metrics card.

    "Current" means the verdict is about the bytes the file holds NOW —
    a finding about an old revision is history, not health. One query for the
    whole set, same reasoning as ``clean_file_ids``.
    """
    from toto.vault.scanning import SCANNABLE_TYPES

    from .models import ScanResult, ScanVerdict

    files = list(files)
    scannable = [f for f in files
                 if f.file_type in SCANNABLE_TYPES and not f.is_encrypted]
    hashes = {f.pk: (f.content_hash or "unreadable") for f in scannable}

    current = {}
    for row in ScanResult.objects.filter(file_id__in=hashes):
        if row.content_sha256 == hashes.get(row.file_id):
            current[row.file_id] = row

    clean = sum(1 for r in current.values() if r.verdict == ScanVerdict.CLEAN)
    threats = sum(1 for r in current.values() if r.verdict == ScanVerdict.REFUSED)
    failed = sum(1 for r in current.values() if r.verdict == ScanVerdict.ERROR)
    unscanned = len(scannable) - len(current)

    if threats:
        status = STATUS_THREATS
    elif failed:
        status = STATUS_ATTENTION
    elif unscanned:
        status = STATUS_PARTIAL
    elif current:
        status = STATUS_CLEAN
    else:
        status = STATUS_NONE

    return {
        "total": len(files),
        "scannable": len(scannable),
        "unscannable": len(files) - len(scannable),
        "scanned": len(current),
        "clean": clean,
        "threats": threats,
        "failed": failed,
        "unscanned": unscanned,
        "scanned_bytes": sum(r.size_bytes for r in current.values()),
        "status": status,
    }
