"""The PDF scanner: refuse active content, never parse the whole file.

A PDF is not a picture of a document — it is a container that may carry
JavaScript, auto-run actions, launched programs and embedded files, and every
one of those is a way for "open this invoice" to become code execution. This
scanner looks for exactly those markers in the raw bytes and refuses when it
finds one. It parses nothing: a parser is attack surface, and the markers are
name tokens that cannot appear in a PDF that does not use the feature.

**What it deliberately does NOT refuse:** ``/AcroForm`` and signature
machinery (``/Sig``, ``/ByteRange``). Signed documents are this platform's own
product — notarius writes them — and a scanner that refuses the signer's
output is a ban, not a guard. The same lesson as the HTML5 doctype.

Case matters: PDF name objects are case-sensitive, so the match is exact and
anchored — ``/JS`` must end at a delimiter, or ``/JSXform`` (a name that merely
starts the same way) would be a false positive.
"""

from __future__ import annotations

import re

from toto.vault.scanning import Verdict

from . import register

REASON_ACTIVE = "active-content"
REASON_SHAPE = "wrong-shape"

#: PDF name tokens that mean the document DOES something. A name token ends at
#: whitespace or a delimiter, which the lookahead enforces.
_ACTIVE_MARKERS = [
    (rb"/JavaScript", "JavaScript"),
    (rb"/JS", "JavaScript action"),
    (rb"/OpenAction", "an auto-run action"),
    (rb"/AA", "additional actions"),
    (rb"/Launch", "a launched program"),
    (rb"/EmbeddedFile", "an embedded file"),
    (rb"/RichMedia", "embedded rich media"),
    (rb"/XFA", "an XFA form"),
]
_DELIMITER = rb"(?![A-Za-z0-9])"

_MARKER_RE = [
    (re.compile(marker + _DELIMITER), label) for marker, label in _ACTIVE_MARKERS
]

#: An encrypted PDF cannot be inspected. Refused rather than waved through —
#: the same rule the engine applies to bytes that are not text: a scanner that
#: passes what it cannot read is marking things it never checked.
_ENCRYPT_RE = re.compile(rb"/Encrypt" + _DELIMITER)


def scan_pdf(data: bytes) -> Verdict:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        return Verdict.refused(REASON_SHAPE, "not bytes")
    raw = bytes(data)

    if not raw.lstrip().startswith(b"%PDF-"):
        return Verdict.refused(REASON_SHAPE, "not a PDF")

    if _ENCRYPT_RE.search(raw):
        return Verdict.refused(
            REASON_SHAPE, "encrypted PDF — its content cannot be inspected")

    for pattern, label in _MARKER_RE:
        match = pattern.search(raw)
        if match is not None:
            line = raw.count(b"\n", 0, match.start()) + 1
            return Verdict.refused(REASON_ACTIVE, label, line=line)

    return Verdict.clean()


register("pdf", scan_pdf, binary=True)
