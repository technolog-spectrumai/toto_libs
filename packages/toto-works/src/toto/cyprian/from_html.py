"""Reading an HTML page: which part of it a document can hold.

Three small functions, and they are all that is left here. The CONVERSION
itself moved to :mod:`toto.cyprian.conversion`, which does the same job while
counting what it drops — and a count is what the person converting actually
needs. There used to be a `convert()` here that adopted a same-named document
instead of making a new one, a `page_of()` that rebuilt a page for the deleted
write-back bridge, and an `is_compatible()` predicate that answered "would this
lose anything" with a boolean. All three are superseded: `Report.lossless` is
the same question with the answer itemised, and two functions that can disagree
about it is one too many.

An HTML page is a WHOLE file — doctype, `<head>`, its own `<style>`, a
`<body>`. A CTML document is a title plus ONE rich-text body
(`ctml.Document.content` is an HTML fragment). The two are not the same thing,
and pretending otherwise is how somebody loses a page's styling without being
told.
"""

from __future__ import annotations

import re
from html import unescape

#: Everything between the body tags. A page with no `<body>` is treated as a
#: fragment and used whole, which is what a hand-written snippet usually is.
_BODY = re.compile(r"<body\b[^>]*>(.*?)</body\s*>", re.I | re.S)
_TITLE = re.compile(r"<title\b[^>]*>(.*?)</title\s*>", re.I | re.S)
#: `<style>` and `<script>` inside the body. The sanitiser drops scripts anyway;
#: a stylesheet is dropped here because it would otherwise be rendered as text.
_DROP = re.compile(r"<(style|script)\b[^>]*>.*?</\1\s*>", re.I | re.S)


def body_of(html: str) -> str:
    """The editable part of a page."""
    match = _BODY.search(html or "")
    body = match.group(1) if match else (html or "")
    return _DROP.sub("", body).strip()


def title_of(html: str, *, fallback: str = "") -> str:
    """What the page calls itself, else the filename without its extension.

    UNESCAPED, and that is a fix rather than a nicety. `htmldoc.page` writes
    the title through `_text`, which turns `&` into `&amp;`; this read it back
    raw, so the escaping survived into `Document.title` and was escaped AGAIN
    on the next save. A document called "Tom & Jerry" became "Tom &amp; Jerry",
    then "Tom &amp;amp; Jerry", growing one `amp;` per save forever.

    That is the accumulation defect this app was known to have. It was
    catalogued as bare `<span>`s piling up in the BODY, and the body is
    provably fine — the sanitiser is idempotent and the client drops unmatched
    spans. It was the title all along, and it is unbounded rather than
    cosmetic.

    `html.unescape` rather than three `str.replace` calls: it is the exact
    inverse of nothing in particular, which is the point — a title may also
    arrive with entities a human typed (`caf&eacute;`), and the reader should
    resolve those the way a browser would. Escaping on write is narrower than
    unescaping on read, deliberately: over-escaping is safe, under-unescaping
    is what compounds.
    """
    match = _TITLE.search(html or "")
    if match:
        title = re.sub(r"\s+", " ", unescape(match.group(1))).strip()
        if title:
            return title
    return fallback.rsplit(".", 1)[0] if "." in fallback else fallback


def document_title(vault_file, html: str) -> str:
    return title_of(html, fallback=vault_file.title)
