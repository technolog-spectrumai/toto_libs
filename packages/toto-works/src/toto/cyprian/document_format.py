"""The document format — self-contained XML, version 3.

A Cyprian document is one XML file holding the whole thing: images embedded as
base64 ``data:`` URIs, formulas with their rendering cached, tables inline.
There is nothing in the database. A document therefore moves, downloads, backs
up and shares with the ordinary vault tools, and it can never half-exist::

    <?xml version="1.0" encoding="utf-8"?>
    <document version="3" title="Quarterly Report"
              font="serif" margins="normal" toc="true">
      <meta>
        <field name="author"><![CDATA[A. Lovelace]]></field>
      </meta>
      <content><![CDATA[<h1>Introduction</h1>
        <p>Revenue is <strong>up</strong>.</p>
        <table><tr><th>Region</th><td>+12%</td></tr></table>]]></content>
    </document>

**One body, and headings are the structure.** Version 1 was a list of typed
blocks, each with its own editor. Version 2 folded those into one rich-text
document per section. Version 3 folds the sections away too: a heading in the
body does everything a section did — it anchors the contents page and it sets
the outline — and it is one keystroke rather than a button, a level and an
ordering to manage.

``loads`` reads all three and converts on the way in; ``dumps`` only ever writes
version 3. **One way**, each time, because the alternative is maintaining three
editors forever.

The rules that came from ``toto.memo.presentation_format`` and still hold:

**The payload is CDATA-wrapped**, including a literal ``]]>`` (split across two
sections so it round-trips). It reads noisier than escaped text and it is the
only rule that cannot lose a byte.

**Nothing unknown is discarded.** Unknown attributes and child elements are
carried through ``dumps`` untouched, and a v1 ``<block>`` whose *type* this build
does not recognise has no HTML to convert into, so it is kept verbatim in
``extra``. It was invisible before the conversion and it is invisible after,
with its bytes intact.

**A4 only.** A document that can be one of two paper sizes needs a control for
it, a rule in every stylesheet and a branch in the paginator — for a choice
almost nobody makes and nobody makes twice.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

# toto.memo owns the allowlists: pure stdlib, no Django, no memo imports, and
# already attacked by memo's own test suite. Copying 300 lines of
# security-critical parsing to avoid one import would be the worse trade — see
# checks.py, which turns a build without memo into a `manage.py check` error
# rather than a 500 on first save.
from toto.memo import sanitize

from .sanitize_html import sanitize_content

FORMAT_VERSION = "3"

# A4 only. A document that can be one of two paper sizes needs a control for it,
# a rule in every stylesheet and a branch in the paginator — for a choice
# almost nobody makes and nobody makes twice. `page` is still PARSED so an older
# file opens; it is simply not written and not offered.
PAGE = "a4"
MARGINS = ("normal", "narrow", "wide")
FONTS = ("sans", "serif", "mono", "rounded", "condensed")

TONES = ("note", "warn", "tip", "quote")

BLOCK_TYPES = (
    "text", "list", "image", "quote", "code", "formula", "table",
    "callout", "pagebreak", "divider", "html",
)

DEFAULT_MARGINS = "normal"
DEFAULT_FONT = "serif"

MAX_SECTIONS = 500
MAX_BLOCKS_PER_SECTION = 400
MAX_ITEMS = 200
MAX_ROWS = 200
MAX_CELLS = 20
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
MAX_LEVEL = 3

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_LANGUAGE_RE = re.compile(r"^[A-Za-z0-9+#._-]{1,20}$")
_DATA_URI_RE = re.compile(
    r"^data:image/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+$", re.IGNORECASE)

_ROOT_OWN_ATTRS = {"version", "title", "font", "page", "margins", "toc", "cover",
                   "numbering"}
_SECTION_OWN_ATTRS = {"id", "level", "color"}
_BLOCK_OWN_ATTRS = {"id", "type"}
# Order matters only for deterministic output.
_BLOCK_ATTR_ORDER = ("ordered", "alt", "language", "cite", "tone", "header",
                     "align", "width", "highlight")


class DocumentParseError(ValueError):
    """Raised when a document cannot be parsed."""


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(4)}"


def _clean_id(value, prefix: str) -> str:
    value = str(value or "")
    return value if _ID_RE.match(value) else _new_id(prefix)


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------

@dataclass
class Block:
    """A version 1 block. Nothing writes one any more.

    It exists so `loads` can read a v1 file and `_blocks_to_html` can fold it
    into a section's content. The editor never sees one.
    """

    id: str = ""
    type: str = "text"
    payload: str = ""                                   # text/quote/code/callout/formula/image
    items: list[str] = field(default_factory=list)      # type="list"
    rows: list[list[str]] = field(default_factory=list)  # type="table"
    render: str = ""                                    # type="formula" — cached KaTeX
    attrs: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        self.id = self.id or _new_id("b")

    def to_dict(self) -> dict:
        return {
            "id": self.id, "type": self.type, "payload": self.payload,
            "items": list(self.items), "rows": [list(r) for r in self.rows],
            "render": self.render, "attrs": dict(self.attrs),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Block":
        raw = data if isinstance(data, dict) else {}
        return cls(
            id=_clean_id(raw.get("id"), "b"),
            type=str(raw.get("type") or "text"),
            payload=str(raw.get("payload") or "")[:MAX_PAYLOAD_BYTES],
            items=[str(i or "") for i in (raw.get("items") or [])][:MAX_ITEMS],
            rows=[[str(c or "") for c in (row or [])][:MAX_CELLS]
                  for row in (raw.get("rows") or [])][:MAX_ROWS],
            render=str(raw.get("render") or "")[:MAX_PAYLOAD_BYTES],
            attrs={str(k): str(v) for k, v in (raw.get("attrs") or {}).items()},
        )


@dataclass
class Document:
    """The whole document: a title, some metadata, and ONE rich-text body.

    Version 2 kept a list of sections, each its own editor. That was still a
    structure to manage — add, order, delete, level — on top of the writing, and
    the writing is the point. A heading in the body does everything a section
    did, including anchoring the contents page, and it is one keystroke rather
    than a button.
    """

    title: str = ""
    content: str = ""
    meta: dict[str, str] = field(default_factory=dict)
    version: str = FORMAT_VERSION
    font: str = DEFAULT_FONT
    margins: str = DEFAULT_MARGINS
    toc: bool = True
    # The cover sheet is OPT-IN: the ordinary cyprian document is a teacher's
    # one-page handout, and a title page would double its length. A report that
    # wants one turns it on in Page setup.
    cover: bool = False
    attrs: dict[str, str] = field(default_factory=dict)
    extra: list[str] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        # A tag boundary is a word boundary: `<h1>Two words</h1><p>three` must
        # not count "wordsthree" as one, which is what stripping tags without
        # replacing them does.
        spaced = re.sub(r"<[^>]+>", " ", self.content or "")
        return len(sanitize.plain_text(spaced).split())

    @property
    def reading_time_minutes(self) -> int:
        """Verbena's own rule, at 220 words a minute."""
        return max(1, round(self.word_count / 220))

    @property
    def outline(self) -> list[dict]:
        """The headings in the body, for the contents page.

        Derived rather than stored: a heading IS the structure now, so there is
        nothing to keep in step. `anchored_content` gives each one the id these
        entries link to.
        """
        return _headings(self.content)

    @property
    def anchored_content(self) -> str:
        """The body with an id on every heading, so the contents page can link.

        The PDF resolves those numbers with `target-counter()` after layout,
        which needs a real anchor to point at.
        """
        return _anchor_headings(self.content)

    def to_dict(self) -> dict:
        return {
            "title": self.title, "version": self.version, "font": self.font,
            "margins": self.margins, "toc": self.toc, "cover": self.cover,
            "content": self.content,
            "meta": dict(self.meta),
            "attrs": dict(self.attrs), "extra": list(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Document":
        """Build from untrusted JSON — **the sanitisation choke point**.

        Every document that arrives from a browser passes through here, so this
        is where the allowlists run. Putting it in the view instead would mean
        the next endpoint somebody adds forgets to call it.
        """
        raw = data if isinstance(data, dict) else {}
        content = str(raw.get("content") or "")
        if not content and raw.get("sections"):
            content = _sections_to_html(raw.get("sections") or [])
        document = cls(
            title=str(raw.get("title") or ""),
            font=str(raw.get("font") or DEFAULT_FONT),
            margins=str(raw.get("margins") or DEFAULT_MARGINS),
            toc=bool(raw.get("toc", True)),
            cover=bool(raw.get("cover", False)),
            version=FORMAT_VERSION,
            content=content[:MAX_PAYLOAD_BYTES],
            meta={str(k): str(v) for k, v in (raw.get("meta") or {}).items()},
            attrs={str(k): str(v) for k, v in (raw.get("attrs") or {}).items()},
            extra=[str(e) for e in (raw.get("extra") or [])],
        )
        _validate(document)
        return document


def new_document(title: str = "") -> Document:
    """A blank document: a heading if it has a name, and somewhere to type.

    Tuned for the ordinary case — a one-page handout. No cover sheet and no
    contents page: the H1 IS the title on the sheet, and both extras are one
    checkbox away in Page setup when a longer document wants them. Existing
    files keep whatever they say (a missing ``toc`` still reads as true there,
    because that was the old default and their exports must not change).
    """
    body = f"<h1>{_esc_text(title)}</h1><p></p>" if title else "<p></p>"
    return Document(title=title, content=body, toc=False, cover=False)


# ---------------------------------------------------------------------------
# Validation and sanitisation
# ---------------------------------------------------------------------------

def _validate(document: Document) -> None:
    """Clamp every value to the schema and sanitise the body, in place.

    Runs on BOTH the save path (`from_dict`) and the open path (`loads`).
    Sanitising on open matters because a document is not only ever written by
    this editor — anyone can upload an XML file to the vault and open it, and a
    public one renders for other people.
    """
    if document.font not in FONTS:
        document.font = DEFAULT_FONT
    if document.margins not in MARGINS:
        document.margins = DEFAULT_MARGINS
    document.meta = {k: sanitize.plain_text(v)[:400]
                     for k, v in document.meta.items() if k}
    document.title = sanitize.plain_text(document.title)[:400]
    # The one sanitisation that matters. See sanitize_html.
    document.content = sanitize_content(document.content, limit=MAX_PAYLOAD_BYTES)


def _unique(value: str, prefix: str, seen: set[str]) -> str:
    while not value or value in seen:
        value = _new_id(prefix)
    seen.add(value)
    return value


def _blocks_to_html(blocks: list[Block]) -> tuple[str, list[Block]]:
    """Fold a version 1 section's blocks into one HTML document.

    Returns the HTML and the blocks that could NOT be converted — a type this
    build has never heard of, which by definition has no markup to become. The
    caller keeps those verbatim so the format's promise holds: nothing unknown
    is discarded.

    The markup produced here is exactly what `_blocks.html` used to render, so a
    converted document reads identically in the reader, the thumbnail and the
    PDF. It is sanitised afterwards like anything else, which is what clamps the
    widths, tones and languages that `_clamp_attrs` used to.
    """
    out: list[str] = []
    unconverted: list[Block] = []

    for block in blocks:
        attrs = block.attrs
        tint = ' class="cy-highlight"' if attrs.get("highlight") == "true" else ""
        kind = block.type

        if kind in ("text", "html"):
            # Already HTML. A highlighted one needs a wrapper to carry the tint.
            out.append(f"<div{tint}>{block.payload}</div>" if tint else block.payload)
        elif kind == "callout":
            tone = attrs.get("tone", "note")
            out.append(f'<div class="cy-callout" data-tone="{_esc_attr(tone)}">'
                       f"{block.payload}</div>")
        elif kind == "quote":
            cite = attrs.get("cite", "")
            inner = block.payload
            if cite:
                inner += f'<p class="cy-cite">{_esc_text(cite)}</p>'
            out.append(f"<blockquote{tint}>{inner}</blockquote>")
        elif kind == "list":
            tag = "ol" if attrs.get("ordered") == "true" else "ul"
            items = "".join(f"<li>{i}</li>" for i in block.items)
            out.append(f"<{tag}{tint}>{items}</{tag}>")
        elif kind == "table":
            head = attrs.get("header") == "true"
            rows = []
            for index, row in enumerate(block.rows):
                cell = "th" if (head and index == 0) else "td"
                rows.append("<tr>" + "".join(f"<{cell}>{c}</{cell}>" for c in row)
                            + "</tr>")
            out.append(f"<table{tint}>" + "".join(rows) + "</table>")
        elif kind == "image":
            extra = ""
            if attrs.get("width"):
                extra += f' data-width="{_esc_attr(attrs["width"])}"'
            if attrs.get("align"):
                extra += f' data-align="{_esc_attr(attrs["align"])}"'
            alt = _esc_attr(attrs.get("alt", ""))
            out.append(f'<img src="{_esc_attr(block.payload)}" alt="{alt}"{extra}>')
        elif kind == "code":
            language = attrs.get("language", "")
            lang = f' data-language="{_esc_attr(language)}"' if language else ""
            # Escaped, never |safe — a code block is literal text, which is what
            # made `<script>alert(1)</script>` safe to *show* in v1 too.
            out.append(f"<pre{lang}{tint}><code>{_esc_text(block.payload)}"
                       "</code></pre>")
        elif kind == "formula":
            latex = _esc_attr(block.payload)
            inner = block.render or (
                f'<code class="cy-formula-source">{_esc_text(block.payload)}</code>')
            out.append(f'<div class="cy-formula" data-latex="{latex}">{inner}</div>')
        elif kind == "divider":
            out.append("<hr>")
        elif kind == "pagebreak":
            out.append('<div class="cy-pagebreak"></div>')
        else:
            unconverted.append(block)

    return "".join(out), unconverted


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def _esc_attr(text: str) -> str:
    return ((text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _esc_text(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;")


def _wrap_cdata(body: str) -> str:
    # A CDATA section cannot contain the literal "]]>"; split it across two
    # sections so the byte sequence round-trips exactly.
    safe = (body or "").replace("]]>", "]]]]><![CDATA[>")
    return f"<![CDATA[{safe}]]>"


def _attr_string(own: list[tuple[str, str]], extra: dict[str, str]) -> str:
    """Known attributes in declared order, then unknown ones sorted.

    Deterministic output is what lets the round-trip test compare strings, and
    what keeps a save from producing a spurious diff.
    """
    parts = [f' {k}="{_esc_attr(v)}"' for k, v in own if v not in ("", None)]
    parts += [f' {k}="{_esc_attr(extra[k])}"' for k in sorted(extra)]
    return "".join(parts)


def dumps(document: Document) -> str:
    root_extra = {k: v for k, v in document.attrs.items()
                  if k not in _ROOT_OWN_ATTRS}
    lines = ['<?xml version="1.0" encoding="utf-8"?>']
    lines.append("<document" + _attr_string(
        [("version", FORMAT_VERSION),
         ("title", document.title),
         ("font", document.font or DEFAULT_FONT),
         ("margins", document.margins or DEFAULT_MARGINS),
         ("toc", "true" if document.toc else "false"),
         ("cover", "true" if document.cover else "false")], root_extra) + ">")

    if document.meta:
        lines.append("  <meta>")
        for name in sorted(document.meta):
            lines.append(f'    <field name="{_esc_attr(name)}">'
                         f"{_wrap_cdata(document.meta[name])}</field>")
        lines.append("  </meta>")

    lines.append(f"  <content>{_wrap_cdata(document.content)}</content>")

    for raw in document.extra:
        lines.append("  " + raw.strip())
    lines.append("</document>")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Headings — the structure, derived rather than stored
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"<h([1-3])(\s[^>]*)?>(.*?)</h\1>", re.IGNORECASE | re.DOTALL)
_ID_ATTR_RE = re.compile(r'\bid="([^"]*)"', re.IGNORECASE)


def _sections_to_html(sections) -> str:
    """A version 2 payload — a list of sections — as one body.

    Only reached when an older tab posts: the editor has not sent sections since
    v3. Folding beats refusing, because refusing loses whatever that tab held.
    """
    parts = []
    for raw in (sections or [])[:MAX_SECTIONS]:
        if not isinstance(raw, dict):
            continue
        try:
            level = max(1, min(MAX_LEVEL, int(raw.get("level") or 1)))
        except (TypeError, ValueError):
            level = 1
        heading = sanitize.sanitize_inline(str(raw.get("heading") or ""))
        if heading:
            parts.append(f"<h{level}>{heading}</h{level}>")
        parts.append(str(raw.get("content") or ""))
    return "".join(parts)


def _headings(content: str) -> list[dict]:
    """Every h1-h3 in the body, in order, with the anchor it will carry.

    A regex rather than a parse, and that is a deliberate limit: the content is
    already sanitised, headings are top-level in practice, and a full parse here
    would be a second HTML implementation to keep in step with the first.
    """
    out: list[dict] = []
    for index, match in enumerate(_HEADING_RE.finditer(content or ""), start=1):
        level, attrs, inner = match.group(1), match.group(2) or "", match.group(3)
        text = sanitize.plain_text(inner).strip()
        if not text:
            continue                    # an empty heading is not a destination
        existing = _ID_ATTR_RE.search(attrs)
        out.append({
            "id": existing.group(1) if existing else f"h-{index}",
            "level": int(level),
            "text": text,
        })
    return out


def _anchor_headings(content: str) -> str:
    """The body with an id on every heading that lacks one.

    The contents page links to these, and `target-counter()` resolves the page
    number after layout — which needs something real to point at.
    """
    counter = {"n": 0}

    def replace(match):
        counter["n"] += 1
        level, attrs, inner = match.group(1), match.group(2) or "", match.group(3)
        if _ID_ATTR_RE.search(attrs):
            return match.group(0)
        return f'<h{level}{attrs} id="h-{counter["n"]}">{inner}</h{level}>'

    return _HEADING_RE.sub(replace, content or "")


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def is_document(xml: "str | bytes") -> bool:
    """True if ``xml`` is a Cyprian document (root ``<document>``)."""
    try:
        if isinstance(xml, bytes):
            xml = xml.decode("utf-8")
        return ET.fromstring(xml).tag == "document"
    except Exception:                                  # noqa: BLE001
        return False


_SNIFF_RE = re.compile(rb"<\s*document\s*[/>]|<\s*document\s", re.IGNORECASE)
_SNIFF_SKIP = re.compile(rb"<\?xml[^>]*\?>|<!--.*?-->|<!DOCTYPE[^>]*>", re.DOTALL)


def sniff_is_document(head: "str | bytes") -> bool:
    """Cheap identity check over the first bytes — for listings.

    The library lists every candidate file; doing that with a full parse means
    reading and parsing every embedded image just to look at one tag.
    """
    if isinstance(head, str):
        head = head.encode("utf-8", errors="ignore")
    head = _SNIFF_SKIP.sub(b"", head[:2048]).lstrip()
    return bool(_SNIFF_RE.match(head))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def loads(text: str) -> Document:
    """Read a document of ANY version. Only version 3 is ever written.

    v1 was a list of typed blocks per section; v2 folded those into one HTML
    body per section; v3 folds the sections themselves into one body, with the
    headings that used to be section titles now inside it. Each step converts on
    the way in and is one way — the alternative is maintaining three editors.
    """
    text = (text or "").strip()
    if not text:
        return new_document()

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise DocumentParseError(f"Invalid document: {exc}") from exc

    if root.tag != "document":
        raise DocumentParseError(f"Expected root <document>, found <{root.tag}>")

    document = Document(
        title=root.get("title") or "",
        font=root.get("font") or DEFAULT_FONT,
        margins=root.get("margins") or DEFAULT_MARGINS,
        toc=(root.get("toc") or "true") != "false",
        cover=(root.get("cover") or "false") == "true",
        version=FORMAT_VERSION,
        attrs={k: v for k, v in root.attrib.items() if k not in _ROOT_OWN_ATTRS},
    )

    seen: set[str] = set()
    parts: list[str] = []
    for child in list(root):
        if child.tag == "meta":
            for field_el in child.findall("field"):
                name = field_el.get("name")
                if name:
                    document.meta[name] = field_el.text or ""
        elif child.tag == "content":
            parts.append((child.text or "")[:MAX_PAYLOAD_BYTES])
        elif child.tag == "section":
            if len(parts) < MAX_SECTIONS:
                body, extra = _parse_section(child, seen)
                parts.append(body)
                document.extra.extend(extra)
        else:
            document.extra.append(_serialise(child))

    document.content = "".join(parts)[:MAX_PAYLOAD_BYTES]
    _validate(document)
    return document


def _serialise(element) -> str:
    return ET.tostring(element, encoding="unicode").strip()


def _parse_section(el, seen: set[str]) -> tuple[str, list[str]]:
    """One `<section>` from v1 or v2, as HTML.

    The section's heading becomes a real heading in the body — an `<h1>`, `<h2>`
    or `<h3>` by its level — which is exactly what it always was on every
    rendered surface. The old section id does NOT come with it: `id` is not in
    the content allowlist, and the contents page mints its own anchors at render
    time anyway (`anchored_content`).
    """
    try:
        level = int(el.get("level") or 1)
    except (TypeError, ValueError):
        level = 1
    level = max(1, min(MAX_LEVEL, level))

    heading = ""
    body: list[str] = []
    extra: list[str] = []
    legacy: list[Block] = []

    for child in list(el):
        if child.tag == "heading":
            heading = sanitize.sanitize_inline(child.text or "")
        elif child.tag == "content":
            body.append((child.text or "")[:MAX_PAYLOAD_BYTES])
        elif child.tag == "block":
            if len(legacy) < MAX_BLOCKS_PER_SECTION:
                legacy.append(_parse_block(child, seen))
        else:
            extra.append(_serialise(child))

    if legacy:
        converted, unknown = _blocks_to_html(legacy)
        body.append(converted)
        # A block type this build cannot render has no HTML to become. Keep the
        # element verbatim — invisible before, invisible after, bytes intact.
        extra.extend(_block_xml(block) for block in unknown)

    html = f"<h{level}>{heading}</h{level}>" if heading else ""
    return html + "".join(body), extra


def _block_xml(block: Block) -> str:
    """A v1 block, back as the XML it came from — for the unconvertible ones."""
    known = [("id", block.id), ("type", block.type)]
    extra = {k: v for k, v in block.attrs.items() if k not in _BLOCK_OWN_ATTRS}
    head = "<block" + _attr_string(known, extra)
    if block.items:
        body = "".join(f"<item>{_wrap_cdata(i)}</item>" for i in block.items)
        return head + ">" + body + "</block>"
    if block.rows:
        rows = "".join("<row>" + "".join(f"<cell>{_wrap_cdata(c)}</cell>"
                                         for c in row) + "</row>"
                       for row in block.rows)
        return head + ">" + rows + "</block>"
    if not block.payload:
        return head + "/>"
    return head + ">" + _wrap_cdata(block.payload) + "</block>"


def _parse_block(el, seen: set[str]) -> Block:
    block = Block(
        id=_unique(_clean_id(el.get("id"), "b"), "b", seen),
        type=el.get("type") or "text",
        attrs={k: v for k, v in el.attrib.items() if k not in _BLOCK_OWN_ATTRS},
    )

    if block.type == "list":
        for item in el.findall("item"):
            if len(block.items) < MAX_ITEMS:
                block.items.append((item.text or "")[:MAX_PAYLOAD_BYTES])
    elif block.type == "table":
        for row_el in el.findall("row")[:MAX_ROWS]:
            block.rows.append([(c.text or "")[:MAX_PAYLOAD_BYTES]
                               for c in row_el.findall("cell")[:MAX_CELLS]])
    else:
        source = el.find("source")
        # `<source>` when present, element text otherwise — so a hand-written
        # `<block type="formula">x^2</block>` still opens.
        block.payload = ((source.text if source is not None else el.text) or "")[
            :MAX_PAYLOAD_BYTES]
        rendered = el.find("render")
        if rendered is not None:
            block.render = (rendered.text or "")[:MAX_PAYLOAD_BYTES]
    return block
