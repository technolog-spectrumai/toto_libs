"""Allowlist sanitisation for a section's rich content.

**This runs on the server, on every save AND on every open, and it is the only
sanitisation that counts.** The writer posts whatever TipTap serialised — or
whatever a hand-written POST chose to send — and a document is rendered for
other people: the reader, the library thumbnail and the PDF all get this HTML.

Why cyprian has its own and does not widen `toto.memo.sanitize`: that allowlist
is **shared with memo's slides**, and a section here needs tables, images,
figures and our own node markup. Loosening the deck sanitiser to fit a document
would be paying for this feature with memo's safety. The parser shape, the
unwrap-never-drop rule and `_safe_href` are memo's, reused verbatim in spirit.

Three rules worth stating, because each is load-bearing:

* **`class` is an allowlist, not a passthrough.** Colour and size are classes
  (`cy-ink-success`, `cy-size-lg`) precisely because `style=` is stripped
  everywhere in this suite — an inline-style mark would vanish on the first
  save. So the classes have to survive, and only ours do.
* **`img[src]` must be a `data:` URI.** Same rule the block format enforced: a
  document is one self-contained file, and a remote `src` would leak the
  reader's IP to whoever hosts it and break the moment that host went away.
* **Unwrap, never drop.** An unknown tag loses the tag and keeps the words.
  Pasting from a word processor should cost you the styling, not the sentence.
"""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser

# Structure and marks a document can hold. `div` is here for our own nodes
# (callout, formula, page break) and for prose pasted from the old Trix era.
ALLOWED_TAGS = {
    # prose
    "p", "div", "span", "br", "hr",
    "strong", "b", "em", "i", "u", "s", "del", "ins", "mark", "code", "a",
    "sub", "sup", "small",
    # structure
    "h1", "h2", "h3", "h4", "blockquote", "pre",
    "ul", "ol", "li",
    # tables
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "colgroup", "col",
    # pictures
    "img", "figure", "figcaption",
}

ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "alt"},
    "th": {"colspan", "rowspan"},
    "td": {"colspan", "rowspan"},
    "col": {"span"},
}

# Every class this app renders. Anything else is dropped — a document cannot
# carry a class the stylesheet has never heard of, and cannot borrow one from
# the surrounding page either.
ALLOWED_CLASSES = {
    "cy-ink-accent", "cy-ink-success", "cy-ink-warn",
    "cy-size-sm", "cy-size-lg",
    "cy-callout", "cy-formula", "cy-formula-source", "cy-pagebreak",
    "cy-cite", "cy-th",
    # KaTeX writes its own; the rendered markup arrives already sanitised by
    # memo.sanitize.sanitize_katex, and these keep it looking like maths.
    "katex", "katex-display", "katex-html", "katex-mathml",
}

# Attributes our own nodes need, and the values they may take. A data attribute
# is not a styling hook here — each one drives a specific CSS rule.
TONES = ("note", "warn", "tip", "quote")
DATA_ATTRS = {
    "data-tone": lambda v: v if v in TONES else None,
    # The LaTeX source of a formula node, kept beside its rendered form so the
    # source survives a round trip through the browser.
    "data-latex": lambda v: v[:4000] or None,
    "data-align": lambda v: v if v in ("left", "center", "right") else None,
    "data-width": lambda v: v if v in ("25", "33", "50", "66", "75", "100") else None,
    "data-language": lambda v: v if _LANGUAGE_RE.match(v or "") else None,
}

# Dropped WITH their contents rather than unwrapped: unwrapping <script> would
# paste the source into the document as visible text, which is worse than
# losing it.
VOID_CONTENT_TAGS = {"script", "style", "iframe", "object", "embed", "template",
                     "noscript", "svg", "math"}

SELF_CLOSING = {"br", "img", "hr", "col", "wbr", "source", "track", "input"}

HREF_SCHEMES = {"http", "https", "mailto"}

_SCHEME = re.compile(r"^\s*([a-z][a-z0-9+.-]*)\s*:", re.IGNORECASE)
_LANGUAGE_RE = re.compile(r"^[A-Za-z0-9+#._-]{1,20}$")
_DATA_URI_RE = re.compile(
    r"^data:image/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+$", re.IGNORECASE)
_SPAN_COUNT = re.compile(r"^\d{1,3}$")


def _esc_text(text: str) -> str:
    """Escape a text node: `&` and `<` only.

    NOT `html.escape`, which also rewrites `>`. A bare `>` is legal in text and
    escaping it would corrupt the one sequence the document format goes out of
    its way to preserve — a literal `]]>`, which `_wrap_cdata` splits across two
    CDATA sections precisely so it survives.
    """
    return (text or "").replace("&", "&amp;").replace("<", "&lt;")


def _safe_href(value: str) -> str | None:
    """A link target, or None if it is not one we are willing to emit."""
    value = (value or "").strip().replace("\x00", "")
    if not value:
        return None
    # Control characters are how `java\tscript:` gets past a naive check.
    value = "".join(ch for ch in value if ord(ch) >= 0x20 or ch == " ").strip()
    match = _SCHEME.match(value)
    if match is None:
        return value                       # relative or fragment: fine
    return value if match.group(1).lower() in HREF_SCHEMES else None


def _safe_src(value: str) -> str | None:
    """Pictures live IN the file. A remote src is refused, not rewritten."""
    value = (value or "").strip()
    return value if _DATA_URI_RE.match(value) else None


def _safe_classes(value: str) -> str | None:
    kept = [c for c in (value or "").split() if c in ALLOWED_CLASSES]
    return " ".join(kept) or None


class _Cleaner(HTMLParser):
    def __init__(self):
        # convert_charrefs decodes entities into handle_data, so output escaping
        # is the single place they are re-encoded and nothing double-escapes.
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._suppress: list[tuple[str, int]] = []
        # Tags we let through the allowlist but then refused on their
        # attributes — a link with no target, say. Their END tag has to be
        # dropped too, or the output carries a stray `</a>` and the parse tree
        # the browser builds is not the one we sanitised.
        self._refused: list[tuple[str, int]] = []
        self._depth = 0

    # -- attributes -------------------------------------------------------
    def _attrs(self, tag: str, attrs) -> str:
        keep = ALLOWED_ATTRS.get(tag, set())
        parts: list[str] = []
        has_src = False
        for name, raw in attrs:
            name = (name or "").lower()
            value = raw or ""
            if name == "class":
                value = _safe_classes(value)
            elif name in DATA_ATTRS:
                value = DATA_ATTRS[name](value.strip())
            elif name not in keep:
                continue                                   # every on*, style, id
            elif name == "href":
                value = _safe_href(value)
            elif name == "src":
                value = _safe_src(value)
                has_src = value is not None
            elif name in ("colspan", "rowspan", "span"):
                value = value if _SPAN_COUNT.match(value.strip()) else None
            if value is None:
                continue
            parts.append(f' {name}="{escape(value, quote=True)}"')
        if tag == "img" and not has_src:
            # A picture with no usable source is nothing at all.
            return None
        if tag == "a" and not any(p.startswith(' href=') for p in parts):
            # A link with no target is just text; the words stay, the tag goes.
            return None
        return "".join(parts)

    # -- HTMLParser -------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        self._depth += 1
        if tag in VOID_CONTENT_TAGS:
            # (tag, depth) rather than a counter: a nested </b> inside a
            # suppressed <script> must not end the suppression early.
            self._suppress.append((tag, self._depth))
            return
        if self._suppress or tag not in ALLOWED_TAGS:
            return                                          # unwrap
        rendered = self._attrs(tag, attrs)
        if rendered is None:
            self._refused.append((tag, self._depth))
            return
        self.out.append(f"<{tag}{rendered}>")

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self._suppress or tag in VOID_CONTENT_TAGS or tag not in ALLOWED_TAGS:
            return
        rendered = self._attrs(tag, attrs)
        if rendered is None:
            return
        self.out.append(f"<{tag}{rendered}>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        depth = self._depth
        self._depth = max(0, self._depth - 1)
        if self._suppress and self._suppress[-1] == (tag, depth):
            self._suppress.pop()
            return
        if self._refused and self._refused[-1] == (tag, depth):
            self._refused.pop()
            return
        if self._suppress or tag not in ALLOWED_TAGS or tag in SELF_CLOSING:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self._suppress:
            self.out.append(_esc_text(data))

    def handle_comment(self, data):
        pass                                                # never survives

    def value(self) -> str:
        return "".join(self.out)


def sanitize_content(html: str, *, limit: int = 8 * 1024 * 1024) -> str:
    """A section's content, as it is safe to store and to render.

    Truncate rather than raise on something enormous: a corrupt or hostile file
    must still open, which is the same rule every cap in `document_format`
    follows.
    """
    text = html or ""
    if len(text) > limit:
        text = text[:limit]
    cleaner = _Cleaner()
    cleaner.feed(text)
    cleaner.close()
    return cleaner.value()
