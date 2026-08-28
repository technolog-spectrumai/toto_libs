"""Turn a stored document body into HTML a person can edit by hand.

A cyprian body is one physical line — the format wraps it in a single CDATA
section and the sanitizer emits no whitespace of its own, both on purpose, and
both tested to the byte. That is right for storage and useless for authoring: a
template is something you open and type Jinja tags into, and you cannot find the
place to type inside a 40 kB line with a base64 image in the middle of it.

So this module does not touch storage. It takes a body on its way *out* and
returns two things:

* **indented HTML** — block elements on their own lines, inline runs left alone
  so nothing gains whitespace where whitespace is visible;
* **the images, lifted out** — each `data:` URI replaced by a short name, with
  the bytes handed back separately.

The second half is what makes the first half worth anything. An embedded image
is one attribute holding tens of thousands of base64 characters; no amount of
indentation makes a document containing one readable.

Nothing here knows what the caller wants the placeholder to look like — the
`namer` argument decides that. Aralia asks for ``{{ asset('logo') }}``; cyprian's
own export asks for the picture back. Neither spelling belongs in this module.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from html.parser import HTMLParser

from toto.antivirus.sanitize import SELF_CLOSING

#: Elements that own a line. Everything else is inline and is emitted in the
#: flow of the text, because a newline inside a sentence is a space in HTML and
#: `<em>` gaining one would move the words on the page.
BLOCK_TAGS = {
    "p", "div", "h1", "h2", "h3", "h4", "blockquote", "pre",
    "ul", "ol", "li", "hr", "figure", "figcaption",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "colgroup", "col",
}

#: Never reindented: whitespace inside these is content.
LITERAL_TAGS = {"pre"}

INDENT = "  "

_DATA_URI = re.compile(
    r"^data:image/(?P<subtype>[a-z0-9.+-]+);base64,(?P<payload>[A-Za-z0-9+/=\s]+)$",
    re.IGNORECASE)

#: Extensions for the subtypes the sanitizer will actually let through.
_EXTENSIONS = {"jpeg": "jpg", "svg+xml": "svg"}


@dataclass(frozen=True)
class Image:
    """One picture lifted out of a body."""

    name: str
    data: bytes
    filename: str
    alt: str = ""


def _esc_attr(value: str) -> str:
    """Escape an attribute value, leaving the apostrophe alone.

    `html.escape` would rewrite `'` to `&#x27;`, and the whole point of the
    `namer` hook is that a caller can put something meaningful in a src —
    ``{{ asset('logo') }}`` is Jinja source, and `&#x27;` is not a quote to
    Jinja, so escaping it would hand Aralia a template that cannot compile.
    Attributes are emitted double-quoted, so `"` is the only quote that matters.
    This is ctml._esc_attr's rule.
    """
    return ((value or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _decode(src: str) -> tuple[bytes, str] | None:
    """``data:`` URI to (bytes, extension), or None if it is not one."""
    match = _DATA_URI.match((src or "").strip())
    if match is None:
        return None
    subtype = match.group("subtype").lower()
    # The sanitizer's regex tolerates whitespace inside the payload, so a body
    # that has been through a line-wrapping editor still decodes.
    payload = re.sub(r"\s+", "", match.group("payload"))
    try:
        data = base64.b64decode(payload, validate=True)
    except (ValueError, TypeError):
        return None
    if not data:
        return None
    return data, _EXTENSIONS.get(subtype, subtype)


class _Extractor(HTMLParser):
    """Replaces every embedded picture with a name, keeping the bytes."""

    def __init__(self, namer):
        super().__init__(convert_charrefs=True)
        self._namer = namer
        self.out: list[str] = []
        self.images: list[Image] = []

    def _name_for(self, index: int, alt: str) -> str:
        """Prefer the alt text — a person wrote it, so it means something."""
        base = re.sub(r"[^a-z0-9]+", "-", (alt or "").lower()).strip("-")[:40]
        base = base or f"image-{index}"
        taken = {image.name for image in self.images}
        if base not in taken:
            return base
        n = 2
        while f"{base}-{n}" in taken:
            n += 1
        return f"{base}-{n}"

    def handle_starttag(self, tag, attrs):
        if tag != "img":
            self.out.append(self._tag(tag, attrs))
            return

        values = dict(attrs)
        decoded = _decode(values.get("src") or "")
        if decoded is None:
            # A src we cannot decode is left exactly as it was: this module
            # rewrites pictures, it does not police them.
            self.out.append(self._tag(tag, attrs))
            return

        data, extension = decoded
        alt = values.get("alt") or ""
        name = self._name_for(len(self.images) + 1, alt)
        self.images.append(
            Image(name=name, data=data, filename=f"{name}.{extension}", alt=alt))
        values["src"] = self._namer(name)
        self.out.append(self._tag(tag, list(values.items())))

    handle_startendtag = handle_starttag

    def handle_endtag(self, tag):
        if tag not in SELF_CLOSING:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        self.out.append((data or "").replace("&", "&amp;").replace("<", "&lt;"))

    def handle_comment(self, data):
        self.out.append(f"<!--{data}-->")

    @staticmethod
    def _tag(tag: str, attrs) -> str:
        rendered = "".join(
            f" {name}" if value is None else f' {name}="{_esc_attr(value)}"'
            for name, value in attrs)
        return f"<{tag}{rendered}>"

    def result(self) -> str:
        return "".join(self.out)


def extract_images(html: str, *, namer=None) -> tuple[str, list[Image]]:
    """Lift embedded pictures out of a body.

    Returns the body with each `data:` URI replaced by ``namer(name)``, and the
    pictures in the order they appeared. The default namer leaves the bare name,
    which is only useful for counting them — real callers pass a spelling.
    """
    extractor = _Extractor(namer or (lambda name: name))
    extractor.feed(html or "")
    extractor.close()
    return extractor.result(), extractor.images


class _Indenter(HTMLParser):
    """Puts block elements on their own lines and leaves the prose alone."""

    def __init__(self, indent: str = INDENT):
        super().__init__(convert_charrefs=True)
        self._indent = indent
        self.lines: list[str] = []
        self._depth = 0
        self._current: list[str] = []
        self._literal = 0          # inside <pre>: emit verbatim, never reindent

    # -- line assembly ----------------------------------------------------

    def _flush(self):
        text = "".join(self._current)
        self._current = []
        if text.strip():
            self.lines.append(f"{self._indent * self._depth}{text.strip()}")

    def _emit_block(self, markup: str):
        self._flush()
        self.lines.append(f"{self._indent * self._depth}{markup}")

    # -- parser hooks -----------------------------------------------------

    def handle_starttag(self, tag, attrs):
        markup = _Extractor._tag(tag, attrs)
        if self._literal:
            self._current.append(markup)
            if tag in LITERAL_TAGS:
                self._literal += 1
            return
        if tag in LITERAL_TAGS:
            self._flush()
            self._literal = 1
            self._current.append(f"{self._indent * self._depth}{markup}")
            return
        if tag in BLOCK_TAGS:
            self._emit_block(markup)
            if tag not in SELF_CLOSING:
                self._depth += 1
            return
        self._current.append(markup)

    def handle_startendtag(self, tag, attrs):
        # `<br/>` normalises to `<br>`, the form the sanitizer emits.
        self.handle_starttag(tag, attrs)
        if tag not in SELF_CLOSING and tag in BLOCK_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in SELF_CLOSING:
            return
        if self._literal:
            self._current.append(f"</{tag}>")
            if tag in LITERAL_TAGS:
                self._literal -= 1
                if not self._literal:
                    self.lines.append("".join(self._current))
                    self._current = []
            return
        if tag in BLOCK_TAGS:
            # Flush BEFORE the dedent: the pending text was collected inside
            # this element and belongs at its level, not its parent's.
            self._flush()
            self._depth = max(0, self._depth - 1)
            self._emit_block(f"</{tag}>")
            return
        self._current.append(f"</{tag}>")

    def handle_data(self, data):
        if self._literal:
            self._current.append(data or "")
            return
        text = (data or "").replace("&", "&amp;").replace("<", "&lt;")
        # Runs of whitespace between blocks are the format's, not the author's.
        if text.strip():
            self._current.append(text)
        elif self._current:
            self._current.append(" ")

    def handle_comment(self, data):
        if self._literal:
            self._current.append(f"<!--{data}-->")
        else:
            self._emit_block(f"<!--{data}-->")

    def result(self) -> str:
        self._flush()
        return "\n".join(self.lines)


def pretty(html: str, *, indent: str = INDENT) -> str:
    """Indent a body for reading. Content-preserving, whitespace only.

    `<pre>` is passed through untouched — whitespace inside it is the document,
    not the formatting.
    """
    indenter = _Indenter(indent)
    indenter.feed(html or "")
    indenter.close()
    return indenter.result()


def for_authoring(html: str, *, namer=None, indent: str = INDENT
                  ) -> tuple[str, list[Image]]:
    """Both halves, in the order that matters.

    Images come out first so the indenter never sees a 40 kB attribute — which
    is what turns "indented" into "actually readable".
    """
    body, images = extract_images(html, namer=namer)
    return pretty(body, indent=indent), images
