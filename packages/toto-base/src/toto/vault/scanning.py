"""Screening file content, from the storage side.

``toto.vault`` is a core app on every host; ``toto.antivirus`` is optional
(``BUILD_ANTIVIRUS``). So nothing here may name that app outside a function
body, and every function degrades to a *clean, unscanned* answer rather than
raising — a caller never needs a guard::

    verdict = scanning.scan(data, file_type="svg")
    if not verdict.ok:
        return JsonResponse({"error": verdict.reason, ...}, status=400)

The one exception let through is a celery worker's soft time limit: it is the
worker's, not the scanner's, and swallowed here it would land a file unscanned
and run the task on into the hard kill.

The same trick as :mod:`toto.quota.rates`, for the same reason.

**Read the degradation carefully, because it is a real trade.** With the app
off, ``scanned`` is False and ``ok`` is True: content is written unscreened and
silently. That is what making antivirus optional buys, and it is why `scanned`
is a separate field from `ok` — a caller that cares about the difference between
"we looked and it was fine" and "we never looked" can tell them apart, and the
UI does exactly that (an unscanned file is marked in no way at all).

**Only plain data crosses the boundary.** A `Verdict` is a frozen dataclass of
strings and ints; no model instance is ever returned, because a template that
could reach ``verdict.result.file.bucket`` would blow up on precisely the hosts
this indirection protects.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.apps import apps

try:  # celery is optional in toto-base; without it nothing sends this
    from celery.exceptions import SoftTimeLimitExceeded
except ImportError:  # pragma: no cover
    class SoftTimeLimitExceeded(Exception):
        pass

#: What this platform knows how to screen. Images, video and audio are
#: deliberately absent: they are opaque blobs to a text scanner, and pretending
#: to have looked at them would be worse than saying nothing.
# "pdf" is the one binary member: its scanner reads raw bytes for the marker
# tokens of active content (JavaScript, auto-run actions, launched programs)
# and parses nothing.
# "pxml" (slide decks) has its own scanner rather than riding on "xml": a deck
# CDATA-wraps every block payload, and the xml scanner refuses CDATA outright,
# so decks were being refused unread at every write door. See markup.scan_pxml.
SCANNABLE_TYPES = ("svg", "html", "xml", "json", "pdf", "pxml")


@dataclass(frozen=True)
class Verdict:
    """The answer to "may this content be stored?" — plain data, always."""

    ok: bool
    #: False means nobody looked. Distinct from ok on purpose.
    scanned: bool = False
    #: A short machine name: "active-content", "external-reference", "malformed".
    reason: str = ""
    #: One human sentence naming what was found.
    detail: str = ""
    #: 1-indexed, when the scanner can say where. 0 when it cannot.
    line: int = 0

    @classmethod
    def clean(cls, *, scanned: bool = True) -> "Verdict":
        return cls(ok=True, scanned=scanned)

    @classmethod
    def refused(cls, reason: str, detail: str = "", line: int = 0) -> "Verdict":
        return cls(ok=False, scanned=True, reason=reason, detail=detail, line=line)

    def as_error(self) -> dict:
        """The JSON body a refusing endpoint returns."""
        return {"error": "Refused.", "reason": self.reason,
                "detail": self.detail, "line": self.line}


def scanning_enabled() -> bool:
    """True when this host has a scanner to ask."""
    return apps.is_installed("toto.antivirus")


def is_scannable(file_type: str) -> bool:
    """Whether we have any scanner for this type at all."""
    return file_type in SCANNABLE_TYPES


def scan_size_cap_bytes():
    """The scanner's own size ceiling in bytes, or None where there is none.

    Callers need this because the engine refuses anything larger as
    "wrong-shape" BEFORE decoding — which is right for a document somebody is
    about to open, and wrong as a blanket rule for a caller whose whole subject
    is large files. toto.ocr reads scanned books: the default cap is 10 MB and
    an ordinary book is 19 MB, so asking the scanner directly would refuse
    precisely the files that feature exists for.

    Ask this FIRST and decide; never string-match the refusal detail afterwards.
    A caller over the cap should record the file as clean-and-UNSCANNED and say
    so out loud — "we checked" and "we could not check" must not look alike.

    None where toto.antivirus is absent, matching this module's contract that
    nothing here raises and an absent scanner degrades rather than blocks.
    """
    if not scanning_enabled():
        return None
    try:
        from toto.antivirus.scanners.config import params

        return int(float(params().get("scan_max_mb") or 10) * 1024 * 1024)
    except Exception:  # noqa: BLE001 — an unreadable config is not a refusal
        return None


def scan(data, *, file_type: str, filename: str = "") -> Verdict:
    """Screen content before it is stored. Never raises (but see above).

    ``data`` may be bytes or str; the scanner decodes. A type nothing screens,
    or a host with no antivirus, comes back clean-and-unscanned.
    """
    if not scanning_enabled() or not is_scannable(file_type):
        return Verdict.clean(scanned=False)
    try:
        from toto.antivirus import engine
    except ImportError:  # pragma: no cover - app installed, module missing
        return Verdict.clean(scanned=False)
    try:
        return engine.scan(data, file_type=file_type, filename=filename)
    except SoftTimeLimitExceeded:
        raise
    except Exception:  # noqa: BLE001
        # A scanner that crashes must not turn somebody's save into a 500. It
        # also must not silently pass hostile content off as screened — so this
        # is clean-but-UNSCANNED, which marks nothing and blocks nothing.
        return Verdict.clean(scanned=False)


def record(vault_file, verdict: Verdict, *, user=None, door: str = "") -> None:
    """Remember what a scan concluded about a stored file. Never raises
    (but see above)."""
    if not scanning_enabled() or not verdict.scanned:
        return
    try:
        from toto.antivirus import engine

        engine.record(vault_file, verdict, user=user, door=door)
    except SoftTimeLimitExceeded:
        raise
    except Exception:  # noqa: BLE001 - bookkeeping must not break a save
        return


def clean_file_ids(files) -> set[int]:
    """Which of these files have a clean verdict for the bytes they hold NOW.

    One query for a whole listing, not one per row — this is called from file
    lists, and the tick is not worth an N+1. An empty set on a host with no
    antivirus, so a listing renders exactly as it does today.
    """
    if not scanning_enabled():
        return set()
    try:
        from toto.antivirus import engine

        return engine.clean_file_ids(files)
    except Exception:  # noqa: BLE001
        return set()


def health_report(files):
    """Antivirus health over a set of files, or None where there is no antivirus.

    The bucket metrics page is the caller: the vault may not import the
    antivirus app, so the counting happens engine-side and this façade answers
    ``None`` on a host without it — the metrics card simply does not render,
    which is the honest state, not a card full of zeroes about a scanner that
    does not exist.
    """
    if not scanning_enabled():
        return None
    try:
        from toto.antivirus import engine

        return engine.health_report(files)
    except Exception:  # noqa: BLE001 - a metrics card is not worth a 500
        return None


def should_scan(owner, file_type: str, door: str = "") -> bool:
    """Whether an automatic door should screen this file, per its OWNER's
    preference.

    The owner's, not the actor's: "a preference can only narrow your own
    files" means the person whose file it is decides, and somebody else
    editing a shared file cannot switch the owner's screening off by carrying
    a looser preference of their own. Every failure direction answers True —
    scanning more is always the safe mistake.
    """
    if not scanning_enabled() or not is_scannable(file_type):
        return False
    try:
        from toto.antivirus.models import ScanPreference

        return ScanPreference.applies(owner, file_type, door)
    except SoftTimeLimitExceeded:
        raise
    except Exception:  # noqa: BLE001
        return True
