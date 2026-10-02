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
from xml.etree import ElementTree

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

#: The one declaration that is INERT: the bare HTML5 doctype. It names no
#: external DTD (no SYSTEM/PUBLIC), carries no internal subset (no ``[``) and
#: expands nothing — it is a rendering-mode switch, and every real HTML file
#: starts with it. Refusing it meant no ordinary .html could pass the upload
#: door at all, which turned the scanner from a guard into a ban. Anything
#: beyond the bare form — an identifier, a subset, an entity — falls outside
#: this pattern and is refused exactly as before. SVG and XML keep refusing
#: every doctype: their parsers actually process DTDs, and that is where XXE
#: lives.
_HTML5_DOCTYPE = re.compile(r"<!doctype\s+html\s*>", re.IGNORECASE)

#: What an SVG may point at: itself, or an image it carries.
_SVG_SAFE_REF = re.compile(r"^(#|data:image/(png|jpeg|webp|gif);base64,)",
                           re.IGNORECASE)

#: A scheme we refuse everywhere. `vbscript:` is not a joke on old renderers.
_ACTIVE_URL = re.compile(r"^\s*(javascript|vbscript|livescript)\s*:", re.IGNORECASE)

#: What a browser removes from a URL before it reads the scheme: ASCII tab and
#: newline anywhere, and control characters and spaces around it. The parser
#: decodes `java&#x09;script:` to `java<TAB>script:`, which the pattern above
#: would not match, so the check runs on the value with all of them taken out
#: (htmlview's scrub does the same with its _URL_NOISE).
_URL_NOISE = re.compile(r"[\x00-\x20\x7f]")

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

            if _ACTIVE_URL.match(_URL_NOISE.sub("", value)):
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
    # The bare HTML5 doctype is dropped BEFORE the declaration check — once,
    # from a str only. Everything else about the pipeline is unchanged, so a
    # doctype with a subset or an identifier still refuses on the same line.
    if isinstance(text, str):
        text = _HTML5_DOCTYPE.sub("", text, count=1)
    return _scan_markup(text, svg=False, links_allowed=True)


def scan_xml(text: str) -> Verdict:
    return _scan_markup(text, svg=False, links_allowed=True)


#: A deck's block payloads. `presentation_format.dumps` CDATA-wraps every one.
_CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)


def scan_pxml(text: str) -> Verdict:
    """Screen a slide deck: its character data as HTML, its skeleton as XML.

    A deck may NOT be screened with `scan_xml`, and the reason is the CDATA rule
    above. Refusing CDATA outright is right for anonymous XML — `html.parser`
    does not look inside it and a browser's `DOMParser` does, and refusing
    closes that differential without betting on which is right. But a deck
    CDATA-wraps every single block payload, so `scan_xml` refuses every deck
    ever written, unread, for its envelope rather than its contents.

    So the differential is closed the other way, by looking. The rule that
    matters: **screen what the RENDERER will receive, not what the file looks
    like.** The renderer parses this file as XML and hands each block's decoded
    text to the template, so that decoded text is what gets scanned — which is
    why this parses rather than pattern-matching for `<![CDATA[`.

    Screening CDATA sections specifically was the first version of this
    function, and it was bypassable: `<block>&lt;script&gt;…</block>` carries no
    CDATA at all, so nothing screened it, while the skeleton scan saw only
    entity references and passed it — and XML decodes those references straight
    back into a live `<script>` for the template. CDATA and entity-escaping are
    two spellings of one thing (character data), and the parser is what makes
    them one thing again.

    Order matters. Declarations are refused before any parse, so no entity
    expansion can happen; the parse is what proves the file well-formed; and the
    skeleton scan runs last, on a document already known to have balanced CDATA.
    """
    if not isinstance(text, str) or not text.strip():
        return Verdict.refused(REASON_SHAPE, "empty document")

    # Before the parser sees it — this is what keeps ElementTree safe below.
    match = _DECL_RE.search(text)
    if match is not None:
        return Verdict.refused(
            REASON_DECLARATION, "a document declaration (entity expansion)",
            line=text.count("\n", 0, match.start()) + 1)

    try:
        root = ElementTree.fromstring(text)
    except Exception:  # noqa: BLE001 - a parse error is not a threat
        return Verdict.refused(REASON_SHAPE, "could not be parsed")

    if root.tag != "presentation":
        return Verdict.refused(
            REASON_SHAPE, f"root is <{root.tag}>, expected <presentation>")

    # Every piece of character data, however it was spelled in the file. The
    # parser has already turned CDATA sections and `&lt;` alike into text, which
    # is precisely the form the template renders.
    for element in root.iter():
        for chunk in (element.text, element.tail):
            if not chunk or not chunk.strip():
                continue           # empty or whitespace: nothing to screen
            verdict = _scan_markup(chunk, svg=False, links_allowed=True)
            if not verdict.ok:
                return Verdict.refused(
                    verdict.reason, f"in <{element.tag}>: {verdict.detail}")

    # And the deck itself, for anything hostile written as real markup rather
    # than as content. CDATA is lifted out first so it does not meet the blanket
    # refusal; the document is well-formed by now, so those spans are balanced.
    return _scan_markup(_CDATA.sub("", text), svg=False, links_allowed=True)


register("svg", scan_svg)
register("html", scan_html)
register("xml", scan_xml)
register("pxml", scan_pxml)
