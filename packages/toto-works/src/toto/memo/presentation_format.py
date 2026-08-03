"""Presentation document format — self-contained XML, version 2.

A presentation is one XML document holding an entire slideshow. Raster images
are embedded as base64 ``data:`` URIs and SVGs are inlined verbatim, so the file
has no external dependencies: it *is* the presentation. Nothing about it lives
in the database.

Version 2 gives a slide **structure**. A v1 slide was a title and one opaque
blob of hand-written HTML, which is why the old editor was a ``<textarea>`` and
why nothing could be dragged, restyled or laid out. A v2 slide is a *layout* and
an ordered list of typed *blocks*::

    <?xml version="1.0" encoding="utf-8"?>
    <presentation version="2" title="Quarterly review" theme="black">
      <slide id="s-a1b2c3d4" layout="title-content">
        <title>Welcome</title>
        <block id="b-11aa" type="heading" level="2"><![CDATA[Where we landed]]></block>
        <block id="b-11ab" type="text"><![CDATA[<p>Revenue is <strong>up</strong>.</p>]]></block>
        <block id="b-11ac" type="list"><item><![CDATA[EMEA grew]]></item></block>
      </slide>
      <slide id="s-a1b2c3d5" layout="two-column">
        <title>Before and after</title>
        <block id="b-22aa" type="image" slot="left" alt="Render"><![CDATA[data:image/png;base64,...]]></block>
        <block id="b-22ab" type="code" slot="right" language="python"><![CDATA[print(1)]]></block>
      </slide>
    </presentation>

**Every payload is CDATA-wrapped, with no per-type exceptions.** It reads a
little noisier than escaped text, but it is the only rule that cannot lose a
byte, and it reuses the ``]]>``-splitting trick that already made v1 bodies
round-trip exactly.

``<title>`` stays a slide *element* rather than becoming a block: it is what the
filmstrip, the gallery card and the deck outline read, and making it a block
would force every one of those to go hunting through the block list for "the
heading that is really the title".

**v1 files are upgraded in memory and never rewritten on open.** A v1 slide
becomes one ``type="html"`` block, which the player renders through the same
path the old ``{{ slide.body|safe }}`` used — so an old deck presents
byte-identically. The file on disk stays v1 until the user saves.

**Nothing unknown is discarded.** v1's parser read four fields and dropped the
rest of the tree on the floor, so the format could not be extended by hand and a
newer document lost data the moment an older build saved it. Here, unknown
attributes, unknown block types and unknown child elements are all carried
through ``dumps`` unchanged.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from . import sanitize

FORMAT_VERSION = "2"

# A layout is a fixed set of BOXES, and that is the whole geometry model.
#
# Nothing in a deck resizes or repositions a box: you pick a layout, you get its
# boxes, and you put a block in one. That is the trade this format makes on
# purpose — free geometry means every slide is a small layout project, and a
# deck of thirty ends up with thirty slightly different margins. More layouts is
# the answer to "I need a different shape", not draggable corners.
#
# The value is the ordered list of slot names. `""` is a layout's single
# unnamed box, which keeps every one-box layout identical to what v1 wrote.
LAYOUT_SLOTS = {
    "title-content": ("",),
    "section":       ("",),
    "quote":         ("",),
    "full-bleed":    ("",),
    "two-column":    ("left", "right"),
    "three-column":  ("left", "middle", "right"),
    "image-left":    ("media", "body"),
    "image-right":   ("body", "media"),
    "two-row":       ("top", "bottom"),
    "grid":          ("a", "b", "c", "d"),
    "lead":          ("lead", "body"),
}
LAYOUTS = tuple(LAYOUT_SLOTS)
BLOCK_TYPES = ("heading", "text", "list", "image", "svg", "code", "quote",
               "formula", "html")
# Every slot any layout names. A block keeps a slot the current layout does not
# have — changing layout and changing back must not shuffle the deck — but it
# RENDERS in the first box until it does; see slots_for().
SLOTS = tuple(sorted({slot for slots in LAYOUT_SLOTS.values() for slot in slots}))


def slots_for(layout: str) -> tuple[str, ...]:
    """The boxes this layout has, in reading order."""
    return LAYOUT_SLOTS.get(layout, LAYOUT_SLOTS[DEFAULT_LAYOUT])


def resolve_slot(layout: str, slot: str) -> str:
    """Which box a block actually lands in on this layout.

    A block carrying `right` on a one-box layout is not an error and is not
    dropped: it shows in the only box there is, and still says `right` in the
    file, so switching back to two columns puts it where it was.
    """
    slots = slots_for(layout)
    return slot if slot in slots else slots[0]
THEMES = ("black", "white")
# System stacks only, and every one ends in a generic. No webfont is vendored,
# slide.css may not use url() under the hashed-manifest storage, and the PDF is
# rendered by WeasyPrint inside a container — so a family that is not installed
# there would silently fall back to something else and the PDF would stop
# matching the screen. `notarius/render.py` already relies on DejaVu being in the
# image, which is the precedent for naming it.
FONTS = ("sans", "serif", "mono", "rounded", "condensed")

DEFAULT_LAYOUT = "title-content"
DEFAULT_THEME = "black"
DEFAULT_FONT = "sans"

# A corrupt or hostile document must still open, so these truncate rather than
# raise. They exist so one file cannot exhaust memory on the way in.
MAX_SLIDES = 500
MAX_BLOCKS_PER_SLIDE = 40
MAX_ITEMS = 60
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_LANGUAGE_RE = re.compile(r"^[A-Za-z0-9+#._-]{1,20}$")

# Attributes the schema names. Everything else on an element is preserved but
# not interpreted — see the module docstring.
_BLOCK_OWN_ATTRS = {"id", "type", "slot"}
_SLIDE_OWN_ATTRS = {"id", "layout"}
_ROOT_OWN_ATTRS = {"version", "title", "theme", "font"}

# Order matters only for deterministic output.
_BLOCK_ATTR_ORDER = ("level", "ordered", "alt", "fit", "language", "cite", "scale")

# How small auto-fit is allowed to go before it gives up and lets the content
# spill. Below about half size a slide is unreadable from the back of a room, so
# shrinking further would trade one failure for a worse one.
MIN_SCALE = 0.5
MAX_SCALE = 1.6


class PresentationParseError(ValueError):
    """Raised when a presentation document cannot be parsed."""


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(4)}"


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------

@dataclass
class Block:
    id: str = ""
    type: str = "text"
    payload: str = ""                                   # unused for type="list"
    items: list[str] = field(default_factory=list)      # type="list" only
    slot: str = ""
    # type="formula" only: the KaTeX markup for `payload`, cached at save time.
    #
    # WeasyPrint runs no JavaScript, so without this an exported deck loses
    # every formula. The LaTeX source stays the truth — this is derived, and if
    # it fails sanitisation the block falls back to showing the source rather
    # than a blank.
    render: str = ""
    attrs: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        # Minted here rather than only in the parser, so a deck built in Python
        # — the seeder, a test — also serialises with ids and is stable from its
        # FIRST dump rather than its second.
        self.id = self.id or _new_id("b")

    @property
    def scale_css(self) -> str:
        """The resolved multiplier, or "" when this block renders at full size.

        Templates cannot parse `auto:0.80`, and every surface needs the same
        number, so the parsing lives here once.
        """
        raw = (self.attrs.get("scale") or "auto").strip()
        number = raw[5:] if raw.startswith("auto:") else ("" if raw.startswith("auto") else raw)
        if not number:
            return ""
        try:
            value = float(number)
        except (TypeError, ValueError):
            return ""
        # Only an exact 1.0 is a no-op. A manually enlarged block (1.60) must
        # keep its size — auto-fit only ever shrinks, but the override goes both
        # ways.
        return "" if abs(value - 1.0) < 0.005 else f"{value:.2f}"

    def to_dict(self) -> dict:
        return {
            "id": self.id, "type": self.type, "payload": self.payload,
            "items": list(self.items), "slot": self.slot,
            "render": self.render, "attrs": dict(self.attrs),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Block":
        raw = data if isinstance(data, dict) else {}
        attrs = raw.get("attrs")
        return cls(
            id=_clean_id(raw.get("id"), "b"),
            type=str(raw.get("type") or "text"),
            payload=str(raw.get("payload") or "")[:MAX_PAYLOAD_BYTES],
            items=[str(i or "") for i in (raw.get("items") or [])][:MAX_ITEMS],
            slot=str(raw.get("slot") or ""),
            render=str(raw.get("render") or "")[:MAX_PAYLOAD_BYTES],
            attrs={str(k): str(v) for k, v in (attrs or {}).items()},
        )


@dataclass
class Slide:
    id: str = ""
    title: str = ""
    layout: str = DEFAULT_LAYOUT
    blocks: list[Block] = field(default_factory=list)
    attrs: dict[str, str] = field(default_factory=dict)
    extra: list[str] = field(default_factory=list)      # unknown child elements

    def __post_init__(self):
        self.id = self.id or _new_id("s")

    @property
    def body(self) -> str:
        """What v1 called the body — the concatenated raw-HTML blocks.

        Kept as a read-only shim so the player's fallback and the parts of the
        suite that assert on rendered v1 text keep working unchanged.
        """
        return "".join(b.payload for b in self.blocks if b.type == "html")

    @property
    def columns(self) -> list[tuple[str, list["Block"]]]:
        """The flow regions this layout renders, as (slot, blocks) pairs.

        Templates cannot call a method with an argument, and every surface —
        player, thumbnail, PDF — needs the same grouping. Doing it here means
        one definition of "which blocks are in the right-hand column" rather
        than three that drift.

        A block whose slot does not exist in this layout still appears: it falls
        into the FIRST region rather than vanishing, and keeps its own slot in
        the file — so switching a two-column slide to one column and back puts
        every block where it was.
        """
        slots = slots_for(self.layout)
        buckets = {slot: [] for slot in slots}
        for block in self.blocks:
            buckets[resolve_slot(self.layout, block.slot)].append(block)
        return [(slot, buckets[slot]) for slot in slots]

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "layout": self.layout,
            "blocks": [b.to_dict() for b in self.blocks],
            "attrs": dict(self.attrs), "extra": list(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Slide":
        raw = data if isinstance(data, dict) else {}
        blocks = raw.get("blocks")
        if not blocks and raw.get("body"):
            # A v1-shaped payload: `{"title": ..., "body": "<p>…</p>"}`. Only an
            # editor page cached across the deploy sends this, but accepting it
            # costs one branch and the alternative is that person's next save
            # silently emptying their deck.
            blocks = [{"type": "html", "payload": raw["body"]}]
        return cls(
            id=_clean_id(raw.get("id"), "s"),
            title=str(raw.get("title") or ""),
            layout=str(raw.get("layout") or DEFAULT_LAYOUT),
            blocks=[Block.from_dict(b)
                    for b in (blocks or [])][:MAX_BLOCKS_PER_SLIDE],
            attrs={str(k): str(v) for k, v in (raw.get("attrs") or {}).items()},
            extra=[str(e) for e in (raw.get("extra") or [])],
        )


@dataclass
class Presentation:
    title: str = ""
    slides: list[Slide] = field(default_factory=list)
    version: str = FORMAT_VERSION
    theme: str = DEFAULT_THEME
    font: str = DEFAULT_FONT
    attrs: dict[str, str] = field(default_factory=dict)
    extra: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title, "version": self.version, "theme": self.theme,
            "font": self.font,
            "slides": [s.to_dict() for s in self.slides],
            "attrs": dict(self.attrs), "extra": list(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Presentation":
        """Build from untrusted JSON — **the sanitisation choke point**.

        Every document that arrives from a browser passes through here, so this
        is where the allowlists run. Putting it in the view instead would mean
        the next endpoint someone adds forgets to call it.
        """
        raw = data if isinstance(data, dict) else {}
        presentation = cls(
            title=str(raw.get("title") or ""),
            theme=str(raw.get("theme") or DEFAULT_THEME),
            font=str(raw.get("font") or DEFAULT_FONT),
            version=FORMAT_VERSION,       # we always write the current version
            slides=[Slide.from_dict(s)
                    for s in (raw.get("slides") or [])][:MAX_SLIDES],
            attrs={str(k): str(v) for k, v in (raw.get("attrs") or {}).items()},
            extra=[str(e) for e in (raw.get("extra") or [])],
        )
        _validate(presentation, prune=True)
        return presentation


def new_presentation(title: str = "", theme: str = DEFAULT_THEME,
                     font: str = DEFAULT_FONT) -> Presentation:
    """A blank deck with a single empty slide — used for new files."""
    return Presentation(
        title=title, theme=theme, font=font,
        slides=[Slide(id=_new_id("s"), layout=DEFAULT_LAYOUT,
                      blocks=[Block(id=_new_id("b"), type="text")])],
    )


def _clean_id(value, prefix: str) -> str:
    value = str(value or "")
    return value if _ID_RE.match(value) else _new_id(prefix)


# ---------------------------------------------------------------------------
# Validation and sanitisation
# ---------------------------------------------------------------------------

def _validate(presentation: Presentation, *, prune: bool = False) -> None:
    """Clamp every value to the schema and sanitise every payload, in place.

    Out-of-range values fall back to the default rather than raising: a save
    must never fail because a stale browser tab sent an attribute this build
    stopped recognising.

    Runs on BOTH paths — the save path (`from_dict`) and the open path
    (`loads`). Sanitising on open matters because a deck is not only ever
    written by this editor: anyone can upload an XML file to the vault and open
    it as a presentation, and a public one is then rendered in every visitor's
    gallery. `prune` is the one difference — dropping empty slides is right for
    a document a browser just sent, and wrong for a file on disk, where it would
    silently delete a slide somebody left blank on purpose.
    """
    if presentation.theme not in THEMES:
        presentation.theme = DEFAULT_THEME
    if presentation.font not in FONTS:
        presentation.font = DEFAULT_FONT

    seen: set[str] = set()
    for slide in presentation.slides:
        if slide.layout not in LAYOUTS:
            slide.layout = DEFAULT_LAYOUT
        slide.title = sanitize.plain_text(slide.title)
        slide.id = _unique(slide.id, "s", seen)
        for block in slide.blocks:
            block.id = _unique(block.id, "b", seen)
            # A slot no layout names is meaningless and would strand the block
            # in a box that does not exist. One this layout lacks is KEPT — see
            # Slide.columns for why.
            if block.slot not in SLOTS:
                block.slot = ""
            _sanitize_block(block)

    if prune:
        presentation.slides = [s for s in presentation.slides
                               if s.title or s.blocks or s.extra]


def _unique(value: str, prefix: str, seen: set[str]) -> str:
    while not value or value in seen:
        value = _new_id(prefix)
    seen.add(value)
    return value


_DATA_URI_RE = re.compile(
    r"^data:image/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+$", re.IGNORECASE)


def _sanitize_block(block: Block) -> None:
    kind = block.type

    if kind == "text":
        block.payload = sanitize.sanitize_rich(block.payload)
    elif kind in ("heading", "quote"):
        block.payload = sanitize.sanitize_inline(block.payload)
    elif kind == "code":
        # Deliberately untouched. A code block is literal text — running it
        # through the HTML sanitiser would drop `<script>alert(1)</script>` with
        # its contents, silently deleting the very snippet somebody pasted in to
        # show. Safety comes from the render side instead: every surface escapes
        # it (`{{ block.payload }}`, no `|safe`), which is both correct and
        # lossless.
        pass
    elif kind == "list":
        block.items = [sanitize.sanitize_inline(i) for i in block.items][:MAX_ITEMS]
        block.payload = ""
    elif kind == "image":
        # Only a self-contained data: URI. An external src would make the deck
        # stop being self-contained and turn every viewer into a tracked hit.
        if not _DATA_URI_RE.match(block.payload.strip()):
            block.payload = ""
    elif kind == "svg":
        block.payload = sanitize.sanitize_svg(block.payload)
    elif kind == "formula":
        # The source is plain LaTeX and is escaped wherever it is shown, so it
        # needs no HTML sanitising — same reasoning as `code`. The cached render
        # came from a browser and does.
        block.render = sanitize.sanitize_katex(block.render)
    elif kind == "html":
        # The escape hatch, and the only thing a v1 upgrade produces. Left
        # verbatim on purpose: its trust model is exactly what v1's
        # `{{ slide.body|safe }}` already was, and rewriting people's existing
        # hand-written slides would be a worse bargain than leaving it. It is
        # never offered in the editor's "add block" menu, and the gallery
        # renders it escaped rather than live — see the index view.
        pass
    # An unrecognised type keeps its payload untouched and unrendered.

    _clamp_attrs(block)


def _clean_scale(raw: str) -> str:
    """`auto` | `auto:<n>` | `<n>` — intent and measurement in one attribute.

    The editor measures a block against the space it has and writes the result
    back, because the player and the PDF cannot measure and can only render what
    the editor decided. But a block that was *auto* has to stay auto, or it would
    never re-fit when its content changed — so the resolved number rides along
    with the intent rather than replacing it.
    """
    raw = (raw or "auto").strip()
    auto = raw.startswith("auto")
    number = raw[5:] if raw.startswith("auto:") else ("" if auto else raw)
    if not number:
        return "auto"
    try:
        value = float(number)
    except (TypeError, ValueError):
        return "auto"
    value = min(max(value, MIN_SCALE), MAX_SCALE)
    return f"auto:{value:.2f}" if auto else f"{value:.2f}"


def _clamp_attrs(block: Block) -> None:
    attrs = block.attrs
    if "level" in attrs and attrs["level"] not in ("1", "2", "3", "4"):
        attrs["level"] = "2"
    if "fit" in attrs and attrs["fit"] not in ("contain", "cover"):
        attrs["fit"] = "contain"
    if "ordered" in attrs:
        attrs["ordered"] = "true" if attrs["ordered"] == "true" else "false"
    if "language" in attrs and not _LANGUAGE_RE.match(attrs["language"]):
        del attrs["language"]
    if "scale" in attrs:
        attrs["scale"] = _clean_scale(attrs["scale"])
    for key in ("alt", "cite"):
        if key in attrs:
            attrs[key] = sanitize.plain_text(attrs[key])[:200]


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def _esc_attr(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _esc_text(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _wrap_cdata(body: str) -> str:
    # A CDATA section cannot contain the literal "]]>"; split it across two
    # sections so the byte sequence round-trips exactly.
    safe = (body or "").replace("]]>", "]]]]><![CDATA[>")
    return f"<![CDATA[{safe}]]>"


def _attr_string(own: list[tuple[str, str]], extra: dict[str, str]) -> str:
    """Known attributes in declared order, then unknown ones sorted.

    Deterministic output is what lets the round-trip test compare strings, and
    what keeps a save from producing a spurious git diff.
    """
    parts = [f' {k}="{_esc_attr(v)}"' for k, v in own if v not in ("", None)]
    parts += [f' {k}="{_esc_attr(extra[k])}"' for k in sorted(extra)]
    return "".join(parts)


def dumps(presentation: Presentation) -> str:
    root_extra = {k: v for k, v in presentation.attrs.items()
                  if k not in _ROOT_OWN_ATTRS}
    lines = ['<?xml version="1.0" encoding="utf-8"?>']
    lines.append("<presentation" + _attr_string(
        [("version", FORMAT_VERSION),
         ("title", presentation.title),
         ("theme", presentation.theme or DEFAULT_THEME),
         ("font", presentation.font or DEFAULT_FONT)], root_extra) + ">")

    for slide in presentation.slides:
        slide_extra = {k: v for k, v in slide.attrs.items()
                       if k not in _SLIDE_OWN_ATTRS}
        lines.append("  <slide" + _attr_string(
            [("id", slide.id), ("layout", slide.layout or DEFAULT_LAYOUT)],
            slide_extra) + ">")
        lines.append(f"    <title>{_esc_text(slide.title)}</title>")

        for block in slide.blocks:
            block_extra = {k: v for k, v in block.attrs.items()
                           if k not in _BLOCK_OWN_ATTRS
                           and k not in _BLOCK_ATTR_ORDER}
            known = [("id", block.id), ("type", block.type), ("slot", block.slot)]
            known += [(k, block.attrs[k]) for k in _BLOCK_ATTR_ORDER
                      if k in block.attrs]
            head = "    <block" + _attr_string(known, block_extra)
            if block.type == "list":
                lines.append(head + ">")
                for item in block.items:
                    lines.append(f"      <item>{_wrap_cdata(item)}</item>")
                lines.append("    </block>")
            elif block.type == "formula":
                # Both parts as CHILDREN, never as element text beside a child.
                # Mixed content means the payload silently absorbs the
                # indentation whitespace around its sibling — the same reason
                # `list` puts every item in its own element.
                lines.append(head + ">")
                lines.append(f"      <source>{_wrap_cdata(block.payload)}</source>")
                if block.render:
                    lines.append(f"      <render>{_wrap_cdata(block.render)}</render>")
                lines.append("    </block>")
            else:
                lines.append(head + f">{_wrap_cdata(block.payload)}</block>")

        for raw in slide.extra:
            lines.append("    " + raw.strip())
        lines.append("  </slide>")

    for raw in presentation.extra:
        lines.append("  " + raw.strip())
    lines.append("</presentation>")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def is_presentation(xml: "str | bytes") -> bool:
    """True if ``xml`` is a presentation document (root ``<presentation>``).

    A full parse. Correct, and the right thing for a view that is about to
    render the document anyway — but see ``sniff_is_presentation`` for the
    listing path, where this would parse megabytes of base64 to read one tag.
    """
    try:
        if isinstance(xml, bytes):
            xml = xml.decode("utf-8")
        return ET.fromstring(xml).tag == "presentation"
    except Exception:
        return False


_SNIFF_RE = re.compile(rb"<\s*presentation\s*[/>]|<\s*presentation\s", re.IGNORECASE)
_SNIFF_SKIP = re.compile(rb"<\?xml[^>]*\?>|<!--.*?-->|<!DOCTYPE[^>]*>", re.DOTALL)


def sniff_is_presentation(head: "str | bytes") -> bool:
    """Cheap identity check over the first bytes of a file.

    The gallery lists up to a few hundred XML files and has to know which are
    decks. Doing that with a full parse means reading and parsing every embedded
    image in every candidate, which is most of what makes the index slow.
    """
    if isinstance(head, str):
        head = head.encode("utf-8", errors="ignore")
    head = _SNIFF_SKIP.sub(b"", head[:2048]).lstrip()
    return bool(_SNIFF_RE.match(head))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def loads(text: str) -> Presentation:
    text = (text or "").strip()
    if not text:
        return new_presentation()

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise PresentationParseError(f"Invalid presentation document: {exc}") from exc

    if root.tag != "presentation":
        raise PresentationParseError(
            f"Expected root <presentation>, found <{root.tag}>")

    version = root.get("version") or "1"
    # A hand-edit that added blocks but forgot to bump `version` should not be
    # silently flattened back to one HTML blob.
    v2 = version == "2" or root.find(".//block") is not None

    presentation = Presentation(
        title=root.get("title") or "",
        theme=root.get("theme") or DEFAULT_THEME,
        font=root.get("font") or DEFAULT_FONT,
        version=FORMAT_VERSION,
        attrs={k: v for k, v in root.attrib.items() if k not in _ROOT_OWN_ATTRS},
    )
    if presentation.theme not in THEMES:
        presentation.theme = DEFAULT_THEME
    if presentation.font not in FONTS:
        presentation.font = DEFAULT_FONT

    seen: set[str] = set()
    for child in list(root):
        if child.tag != "slide":
            presentation.extra.append(_serialise(child))
            continue
        if len(presentation.slides) >= MAX_SLIDES:
            break
        presentation.slides.append(
            _parse_slide(child, seen) if v2 else _upgrade_slide(child, seen))

    _validate(presentation)
    return presentation


def _serialise(element) -> str:
    return ET.tostring(element, encoding="unicode").strip()


def _parse_slide(el, seen: set[str]) -> Slide:
    slide = Slide(
        id=_unique(_clean_id(el.get("id"), "s"), "s", seen),
        layout=el.get("layout") or DEFAULT_LAYOUT,
        attrs={k: v for k, v in el.attrib.items() if k not in _SLIDE_OWN_ATTRS},
    )
    if slide.layout not in LAYOUTS:
        slide.layout = DEFAULT_LAYOUT

    for child in list(el):
        if child.tag == "title":
            slide.title = child.text or ""
        elif child.tag == "block":
            if len(slide.blocks) < MAX_BLOCKS_PER_SLIDE:
                slide.blocks.append(_parse_block(child, seen))
        else:
            slide.extra.append(_serialise(child))
    return slide


def _parse_block(el, seen: set[str]) -> Block:
    block = Block(
        id=_unique(_clean_id(el.get("id"), "b"), "b", seen),
        type=el.get("type") or "text",
        slot=el.get("slot") or "",
        attrs={k: v for k, v in el.attrib.items() if k not in _BLOCK_OWN_ATTRS},
    )
    if block.slot not in SLOTS:
        block.slot = ""

    if block.type == "list":
        for item in el.findall("item"):
            if len(block.items) < MAX_ITEMS:
                block.items.append((item.text or "")[:MAX_PAYLOAD_BYTES])
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


def _upgrade_slide(el, seen: set[str]) -> Slide:
    """A v1 ``<slide><title/><body/></slide>`` as a v2 slide, in memory only.

    The body becomes exactly one ``html`` block, which the player renders
    through the same unescaped path v1 used — so an upgraded deck presents
    byte-identically to the original. Nothing is written back: the file stays v1
    until the user saves from the editor.
    """
    title_el = el.find("title")
    body_el = el.find("body")
    body = (body_el.text or "") if body_el is not None else ""

    slide = Slide(
        id=_unique(_clean_id(el.get("id"), "s"), "s", seen),
        title=(title_el.text or "") if title_el is not None else "",
        layout=DEFAULT_LAYOUT,
        attrs={k: v for k, v in el.attrib.items() if k not in _SLIDE_OWN_ATTRS},
    )
    if body.strip():
        slide.blocks.append(
            Block(id=_unique("", "b", seen), type="html", payload=body))
    for child in list(el):
        if child.tag not in ("title", "body"):
            slide.extra.append(_serialise(child))
    return slide
