"""
Presentation Markup Language (``.pml``) serialization — self-contained XML format.

A presentation ``.pml`` file is a single self-contained XML document that stores
an entire slideshow: an ordered list of slides, each with a title and an HTML
body.  Raster images are embedded as base64 ``data:`` URIs and SVGs are inlined
verbatim, so the file has no external dependencies — it is the whole
presentation.  Nothing about it lives in the database.

``.pml`` is just XML internally; the dedicated extension is what marks a file as a
presentation in the vault (``VaultFile._EXT_MAP``), the same way ``.tpy`` marks a
notebook — generic ``.xml`` files stay typed ``xml``.

The file is the single source of truth: it is parsed into the in-memory
:class:`Presentation` structure when the viewer/editor opens, and serialized
back when the user presses Save.

Document shape::

    <?xml version="1.0" encoding="utf-8"?>
    <presentation version="1" title="My Talk">
      <slide>
        <title>Welcome</title>
        <body><![CDATA[
          <p>Some HTML</p>
          <img src="data:image/png;base64,iVBORw0KGgo..." alt="Embedded image">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">...</svg>
        ]]></body>
      </slide>
    </presentation>

Slide bodies are wrapped in ``<![CDATA[ ... ]]>`` so pasted HTML, ``<img>`` data
URIs and inline ``<svg>`` stay literal and human-readable in the file.  Each
``<slide>`` maps to one reveal.js ``<section>`` in the viewer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

FORMAT_VERSION = "1"


class PresentationParseError(ValueError):
    """Raised when a presentation document cannot be parsed."""


@dataclass
class Slide:
    title: str = ""
    body: str = ""

    def to_dict(self) -> dict:
        return {"title": self.title, "body": self.body}

    @classmethod
    def from_dict(cls, data: dict) -> "Slide":
        return cls(title=data.get("title") or "", body=data.get("body") or "")


@dataclass
class Presentation:
    title: str = ""
    slides: list[Slide] = field(default_factory=list)
    version: str = FORMAT_VERSION

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "version": self.version,
            "slides": [s.to_dict() for s in self.slides],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Presentation":
        slides = [Slide.from_dict(raw) for raw in data.get("slides") or []]
        return cls(
            title=data.get("title") or "",
            slides=slides,
            version=str(data.get("version") or FORMAT_VERSION),
        )


def new_presentation(title: str = "") -> Presentation:
    """A blank presentation with a single empty slide — used for new files."""
    return Presentation(title=title, slides=[Slide(title="", body="")])


# ---------------------------------------------------------------------------
# Serialization
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


def dumps(presentation: Presentation) -> str:
    lines = ['<?xml version="1.0" encoding="utf-8"?>']
    lines.append(
        f'<presentation version="{_esc_attr(presentation.version or FORMAT_VERSION)}" '
        f'title="{_esc_attr(presentation.title)}">'
    )
    for slide in presentation.slides:
        lines.append("  <slide>")
        lines.append(f"    <title>{_esc_text(slide.title)}</title>")
        lines.append(f"    <body>{_wrap_cdata(slide.body)}</body>")
        lines.append("  </slide>")
    lines.append("</presentation>")
    return "\n".join(lines) + "\n"


def is_presentation(xml: "str | bytes") -> bool:
    """True if ``xml`` is a presentation document (root ``<presentation>``).

    Cheap identity check for "is this ordinary XML vault file a presentation?" —
    used to filter the memo index and gate the viewer/editor now that presentations
    are stored as plain ``.xml`` (``file_type="xml"``) rather than a dedicated type.
    """
    try:
        if isinstance(xml, bytes):
            xml = xml.decode("utf-8")
        return ET.fromstring(xml).tag == "presentation"
    except Exception:
        return False


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
            f"Expected root <presentation>, found <{root.tag}>"
        )

    presentation = Presentation(
        title=root.get("title") or "",
        version=root.get("version") or FORMAT_VERSION,
    )

    for slide_el in root.findall("slide"):
        title_el = slide_el.find("title")
        body_el = slide_el.find("body")
        presentation.slides.append(
            Slide(
                title=(title_el.text or "") if title_el is not None else "",
                body=(body_el.text or "") if body_el is not None else "",
            )
        )

    return presentation
