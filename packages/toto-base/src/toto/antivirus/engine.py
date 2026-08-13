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
    scanner = scanner_for(file_type)
    if scanner is None:
        return Verdict.clean(scanned=False)

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
        return

    ScanResult.objects.update_or_create(
        file=vault_file,
        content_sha256=content_hash,
        defaults={
            "file_type": vault_file.file_type,
            "verdict": ScanVerdict.CLEAN if verdict.ok else ScanVerdict.REFUSED,
            "reason": verdict.reason,
            "detail": verdict.detail[:2000],
            "line": verdict.line,
            "door": door,
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
