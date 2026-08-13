"""Screening SVG, HTML and XML. Detect and refuse; never rewrite.

The rules are delta's `sketch/svg_guard.py`, generalised to three formats and
reimplemented here because that module lives in a tree this one cannot import.
Its design note is worth keeping:

    A cleaner that STRIPS hostile content would break the editor's contract —
    the user would see one thing and save another.

**The three formats do not get the same rules, and flattening them would be a
bug.** An external `href` in an SVG is a tracking beacon and, through `<use>`, a
script-injection vector — SVG is rendered inline, in our origin. An external
`href` in an HTML document is a hyperlink, and refusing it would refuse every
ordinary page. So:

* shared — `<script>`, `<iframe>`, `<object>`, `<embed>`, any `on*` attribute,
  `javascript:` URLs, `<!DOCTYPE>`/`<!ENTITY>`, and CDATA
* SVG only — `<animate>`, `<set>`, `<foreignObject>`, and **any** outward
  reference, including `style="…url(…)"`; the root must be `<svg>`
* HTML — `http(s)` links and images are fine; `data:` is fine only for images
* XML — no link rules at all; it is data, and the danger is entity expansion

The parser is `html.parser` with `convert_charrefs=False`, so entity text stays
inert rather than being expanded into something that then looks harmless.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from toto.vault.scanning import Verdict

from . import register

REASON_ACTIVE = "active-content"
REASON_EXTERNAL = "external-reference"
REASON_DECLARATION = "declaration"
REASON_SHAPE = "wrong-shape"

#: Executable or embedding elements. Refused in every markup format.
_ACTIVE_TAGS = {"script", "iframe", "object", "embed", "applet", "frame",
                "frameset", "handler", "listener"}

#: Additionally refused in SVG. `<foreignObject>` smuggles HTML into a drawing;
#: the animation elements can rewrite an attribute — including `href` — after
#: load, so screening the initial value proves nothing about them.
#:
#: `<use>` is deliberately NOT here, though delta's guard refuses it outright.
#: Every icon sprite in the world is `<use href="#icon">`, and refusing those
#: would make this cry wolf on ordinary files — which is how people learn to
#: ignore a scanner. The real danger is a `<use>` pointing OUTSIDE the document,
#: and the reference rule below already refuses that.
_SVG_EXTRA_TAGS = {"foreignobject", "animate", "animatetransform",
                   "animatemotion", "set"}

#: The billion-laughs family, plus anything else the parser hands us as a
#: declaration. Checked by regex BEFORE parsing, because a parser that has
#: already expanded entities has already lost.
_DECL_RE = re.compile(r"<!DOCTYPE|<!ENTITY", re.IGNORECASE)

#: What an SVG may point at: itself, or an image it carries.
_SVG_SAFE_REF = re.compile(r"^(#|data:image/(png|jpeg|webp|gif);base64,)",
                           re.IGNORECASE)

#: A scheme we refuse everywhere. `vbscript:` is not a joke on old renderers.
_ACTIVE_URL = re.compile(r"^\s*(javascript|vbscript|livescript)\s*:", re.IGNORECASE)

_URL_IN_STYLE = re.compile(r"url\s*\(", re.IGNORECASE)

_REF_ATTRS = ("href", "xlink:href", "src", "xlink:src", "data", "action",
              "formaction", "poster")


class _Found(Exception):
    """First hit wins — unwind rather than collect."""

    def __init__(self, verdict: Verdict):
        self.verdict = verdict
        super().__init__(verdict.reason)


class _Guard(HTMLParser):
    def __init__(self, *, svg: bool, links_allowed: bool):
        # convert_charrefs=False so entity text stays inert.
        super().__init__(convert_charrefs=False)
        self.svg = svg
        self.links_allowed = links_allowed
        self.root_tag = ""
        self._forbidden = _ACTIVE_TAGS | (_SVG_EXTRA_TAGS if svg else set())

    # -- helpers ----------------------------------------------------------

    def _refuse(self, reason: str, detail: str):
        raise _Found(Verdict.refused(reason, detail, line=self.getpos()[0]))

    def _check(self, tag: str, attrs):
        tag = (tag or "").lower()
        if not self.root_tag:
            self.root_tag = tag
        if tag in self._forbidden:
            self._refuse(REASON_ACTIVE, f"<{tag}>")

        for raw_name, raw_value in attrs:
            name = (raw_name or "").lower()
            value = raw_value or ""

            if name.startswith("on"):
                self._refuse(REASON_ACTIVE, f"{name} on <{tag}>")

            if _ACTIVE_URL.match(value):
                self._refuse(REASON_ACTIVE, f"{name} is a script URL on <{tag}>")

            if name == "style" and _URL_IN_STYLE.search(value):
                # In SVG nothing may be fetched at all. In HTML a background
                # image is ordinary, and the scheme check above already caught
                # the dangerous spellings.
                if self.svg:
                    self._refuse(REASON_EXTERNAL, f"style fetches on <{tag}>")

            if name in _REF_ATTRS and value.strip():
                if self.svg and not _SVG_SAFE_REF.match(value.strip()):
                    self._refuse(REASON_EXTERNAL,
                                 f"{name}={value.strip()[:60]!r} on <{tag}>")
                if not self.svg and not self.links_allowed:
                    self._refuse(REASON_EXTERNAL,
                                 f"{name} on <{tag}> in a data document")

    # -- parser hooks -----------------------------------------------------

    def handle_starttag(self, tag, attrs):
        self._check(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        # A self-closing <script/> must not slip past.
        self._check(tag, attrs)

    def handle_decl(self, decl):
        self._refuse(REASON_DECLARATION, decl[:80])

    def unknown_decl(self, data):
        # CDATA. html.parser does not scan inside it; a browser's DOMParser
        # does. Refusing outright closes that differential rather than betting
        # on which one is right.
        self._refuse(REASON_ACTIVE, f"declaration {data[:60]}")


def _scan_markup(text: str, *, svg: bool, links_allowed: bool,
                 require_root: str = "") -> Verdict:
    if not isinstance(text, str) or not text.strip():
        return Verdict.refused(REASON_SHAPE, "empty document")

    match = _DECL_RE.search(text)
    if match is not None:
        line = text.count("\n", 0, match.start()) + 1
        return Verdict.refused(
            REASON_DECLARATION,
            "a document declaration (entity expansion)", line=line)

    guard = _Guard(svg=svg, links_allowed=links_allowed)
    try:
        guard.feed(text)
        guard.close()
    except _Found as found:
        return found.verdict
    except Exception:  # noqa: BLE001 - a parse error is not a threat
        return Verdict.refused(REASON_SHAPE, "could not be parsed")

    if require_root and guard.root_tag != require_root:
        return Verdict.refused(
            REASON_SHAPE, f"root is <{guard.root_tag or 'nothing'}>, expected <{require_root}>")
    return Verdict.clean()


def scan_svg(text: str) -> Verdict:
    return _scan_markup(text, svg=True, links_allowed=False, require_root="svg")


def scan_html(text: str) -> Verdict:
    return _scan_markup(text, svg=False, links_allowed=True)


def scan_xml(text: str) -> Verdict:
    return _scan_markup(text, svg=False, links_allowed=True)


register("svg", scan_svg)
register("html", scan_html)
register("xml", scan_xml)
