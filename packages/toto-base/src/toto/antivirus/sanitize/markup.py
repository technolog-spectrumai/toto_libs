"""Allowlist sanitisation for slide content.

**This runs on the server, on save, and it is the only sanitisation that
counts.** The editor writes slide text through a `contenteditable`, so what
arrives is whatever the browser — or a hand-written POST — chose to send, and a
deck is rendered for other people: a public deck's HTML lands in the gallery and
the player of every visitor. The editor has a matching client-side pass, but
that one exists to keep pasted Word markup tidy, not to keep anyone safe.

Hand-rolled on `html.parser` rather than adding `bleach` or `nh3`: neither is in
any host's requirements, and pulling one in would mean three `requirements.txt`
edits plus a wheel in every image for about 120 lines of allowlist walking.

The rule for a disallowed tag is **unwrap, never drop** — the children survive.
Someone pasting from a word processor gets their words back with the styling
removed, rather than a slide that silently lost a paragraph.
"""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser

# Inline marks an editor can produce, and nothing else. `del` and `ins` stay
# even though the prose field is TipTap now (which writes <s>): Trix wrote
# strikethrough as <del>, and every deck saved in that era still holds it.
INLINE_TAGS = {
    "b", "strong", "i", "em", "u", "s", "del", "ins", "code", "a", "br",
    "span", "sub", "sup", "mark", "small",
}

# Additionally allowed inside a text block, which is a small flow of prose.
#
# TipTap writes <p>, the lists and <blockquote>; <div>, the headings and <pre>
# are what the Trix era left in saved decks (Trix wrote each line as a <div>).
# Dropping the legacy tags would not lose the words — an unknown tag is
# unwrapped, not deleted — but it WOULD silently reflow every old deck the
# moment it was next saved, which reads as the editor eating your formatting.
# Attributes are stripped from all of them regardless, so allowing a tag grants
# no styling power.
BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "blockquote", "pre", "ul", "ol", "li"}

# Kept per tag. Everything else — style, class, id, data-*, and every on* — goes.
ALLOWED_ATTRS = {
    "a": {"href", "title"},
}

# Relative ("/x", "x") and fragment ("#x") hrefs are allowed too; see _safe_href.
HREF_SCHEMES = {"http", "https", "mailto"}

# Dropped with their entire contents rather than unwrapped: unwrapping <script>
# would paste the source into the slide as visible text, which is worse than
# losing it.
VOID_CONTENT_TAGS = {"script", "style", "iframe", "object", "embed", "template"}

SELF_CLOSING = {"br", "img", "hr", "input", "source", "track", "wbr"}

_SCHEME = re.compile(r"^\s*([a-z][a-z0-9+.-]*)\s*:", re.IGNORECASE)


def _esc_text(text: str) -> str:
    """Escape a text node: `&` and `<` only.

    NOT `html.escape`, which also rewrites `>`. A bare `>` in text is legal and
    means nothing to a parser, and escaping it would corrupt the one sequence
    the document format goes out of its way to preserve — a literal `]]>` in a
    slide body, which `_wrap_cdata` splits across two CDATA sections precisely
    so it survives.
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
        # No scheme at all: a relative or fragment link. Those are fine, and
        # they are the common case for a link between slides.
        return value
    return value if match.group(1).lower() in HREF_SCHEMES else None


class _Cleaner(HTMLParser):
    def __init__(self, allowed: set[str]):
        # convert_charrefs decodes entities into handle_data, so output escaping
        # is the single place they are re-encoded and nothing double-escapes.
        super().__init__(convert_charrefs=True)
        self.allowed = allowed
        self.out: list[str] = []
        self._suppress = 0          # depth inside a dropped-with-contents tag

    # -- helpers ----------------------------------------------------------
    def _attrs(self, tag: str, attrs) -> str:
        keep = ALLOWED_ATTRS.get(tag, set())
        parts = []
        for name, value in attrs:
            name = (name or "").lower()
            if name not in keep:
                continue
            if name == "href":
                value = _safe_href(value or "")
                if value is None:
                    continue
            parts.append(f' {name}="{escape(value or "", quote=True)}"')
        if tag == "a" and not any(p.startswith(" href=") for p in parts):
            # A link with no usable target is just text; the tag stays so the
            # user can see something was there, but it cannot navigate.
            return ""
        return "".join(parts)

    # -- HTMLParser -------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in VOID_CONTENT_TAGS:
            self._suppress += 1
            return
        if self._suppress or tag not in self.allowed:
            return                                    # unwrap: emit nothing
        self.out.append(f"<{tag}{self._attrs(tag, attrs)}>")

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self._suppress or tag in VOID_CONTENT_TAGS or tag not in self.allowed:
            return
        self.out.append(f"<{tag}{self._attrs(tag, attrs)}>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in VOID_CONTENT_TAGS:
            self._suppress = max(0, self._suppress - 1)
            return
        if self._suppress or tag not in self.allowed or tag in SELF_CLOSING:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self._suppress:
            self.out.append(_esc_text(data))

    def handle_comment(self, data):
        pass                                          # comments never survive

    def value(self) -> str:
        return "".join(self.out)


def sanitize_inline(html: str) -> str:
    """Inline marks only — for a heading, a quote, or one list item."""
    cleaner = _Cleaner(INLINE_TAGS)
    cleaner.feed(html or "")
    cleaner.close()
    return cleaner.value()


def sanitize_rich(html: str) -> str:
    """Inline marks plus paragraphs and lists — for a text block."""
    cleaner = _Cleaner(INLINE_TAGS | BLOCK_TAGS)
    cleaner.feed(html or "")
    cleaner.close()
    return cleaner.value()


def plain_text(html: str) -> str:
    """Every tag dropped, text kept, entities resolved once."""
    cleaner = _Cleaner(set())
    cleaner.feed(html or "")
    cleaner.close()
    # _Cleaner re-escapes on the way out; a plain-text caller wants the text.
    from html import unescape

    return unescape(cleaner.value())


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------

# `media.clean_svg_markup` used to be four regexes that stripped the prolog, the
# doctype and <script>. Attributes are invisible to a regex, so `<svg
# onload="...">` went straight through it into the player — and the editor's
# client-side path did not even strip <script>. A parser can see attributes.
#
# Stage 51 turned the blocklist that followed into an ALLOWLIST. A browser
# leaves SVG's foreign content at an HTML "breakout" tag (<p>, <div>, <meta>,
# <b>, <font color> and some forty more), so `<svg><p></p><form action=...>`
# put a live HTML form, a meta refresh or a <style> on the Play page, and a
# blocklist cannot list everything HTML has. Now only drawing elements and
# their presentation attributes come out, inside an <svg> root, with every
# element closed; anything else goes with its whole subtree.

SVG_ELEMENTS = {
    "svg", "g", "defs", "symbol", "use", "switch", "title", "desc",
    "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
    "text", "tspan", "textpath",
    "lineargradient", "radialgradient", "stop", "pattern", "clippath", "mask",
    "marker", "filter",
    "feblend", "fecolormatrix", "fecomponenttransfer", "fecomposite",
    "feconvolvematrix", "fediffuselighting", "fedisplacementmap",
    "fedistantlight", "fedropshadow", "feflood", "fefunca", "fefuncb",
    "fefuncg", "fefuncr", "fegaussianblur", "femerge", "femergenode",
    "femorphology", "feoffset", "fepointlight", "fespecularlighting",
    "fespotlight", "fetile", "feturbulence",
    "animate", "animatetransform", "animatemotion", "set", "mpath",
}

# Unwrapped rather than dropped: the drawing inside a link survives, the link
# does not.
SVG_UNWRAP = {"a"}

# Elements a browser never closes (HTML voids, and <image>, which an HTML
# parser reads as <img>). Starting a subtree drop at one would wait for an end
# tag that never comes.
_NEVER_CLOSED = {
    "area", "base", "br", "col", "embed", "hr", "image", "img", "input",
    "keygen", "link", "meta", "param", "source", "track", "wbr",
}

# The animation elements; `attributeName` on one must name an attribute that
# is itself allowed, and never a reference (`href`) or `style`.
SVG_ANIMATE_TAGS = {"animate", "animatetransform", "animatemotion", "set"}

# Presentation properties, usable as attributes and inside `style`.
SVG_PRESENTATION = {
    "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width",
    "stroke-linecap", "stroke-linejoin", "stroke-miterlimit",
    "stroke-dasharray", "stroke-dashoffset", "stroke-opacity", "opacity",
    "clip-path", "clip-rule", "mask", "filter", "marker-start", "marker-mid",
    "marker-end", "color", "display", "visibility", "font-family",
    "font-size", "font-size-adjust", "font-stretch", "font-weight",
    "font-style", "font-variant", "text-anchor", "dominant-baseline",
    "alignment-baseline", "baseline-shift", "letter-spacing", "word-spacing",
    "text-decoration", "writing-mode", "direction", "unicode-bidi",
    "stop-color", "stop-opacity", "flood-color", "flood-opacity",
    "lighting-color", "color-interpolation", "color-interpolation-filters",
    "color-rendering", "shape-rendering", "text-rendering", "image-rendering",
    "vector-effect", "paint-order", "overflow",
}

SVG_ATTRS = SVG_PRESENTATION | {
    # structure and geometry
    "id", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry",
    "fx", "fy", "fr", "width", "height", "d", "points", "dx", "dy", "rotate",
    "offset", "viewbox", "preserveaspectratio", "version", "xmlns",
    "xmlns:xlink", "xml:space", "lang", "xml:lang", "transform", "style",
    "pathlength", "textlength", "lengthadjust", "startoffset", "method",
    "spacing", "side", "href", "xlink:href", "gradientunits",
    "gradienttransform", "spreadmethod", "patternunits",
    "patterncontentunits", "patterntransform", "clippathunits", "maskunits",
    "maskcontentunits", "markerwidth", "markerheight", "markerunits", "refx",
    "refy", "orient", "systemlanguage", "requiredfeatures",
    "requiredextensions", "role", "aria-label", "aria-hidden",
    "aria-labelledby", "aria-describedby",
    # filter primitives
    "in", "in2", "result", "stddeviation", "mode", "operator", "k1", "k2",
    "k3", "k4", "values", "type", "tablevalues", "slope", "intercept",
    "amplitude", "exponent", "basefrequency", "numoctaves", "seed",
    "stitchtiles", "scale", "xchannelselector", "ychannelselector", "radius",
    "surfacescale", "specularconstant", "specularexponent", "diffuseconstant",
    "kernelmatrix", "order", "divisor", "bias", "targetx", "targety",
    "edgemode", "preservealpha", "azimuth", "elevation", "pointsatx",
    "pointsaty", "pointsatz", "limitingconeangle", "filterunits",
    "primitiveunits", "kernelunitlength", "z",
    # animation timing and values
    "attributename", "attributetype", "begin", "dur", "end", "min", "max",
    "restart", "repeatcount", "repeatdur", "calcmode", "keytimes",
    "keysplines", "from", "to", "by", "additive", "accumulate", "path",
    "keypoints",
}

# What an animation may not retarget: a reference, the style, an identity.
_ANIMATE_FORBIDDEN = {"href", "xlink:href", "style", "id", "attributename"}

# Every `url(` in a value must be a same-document one, `url(#id)`; an external
# one is a tracking beacon at best.
_URL_FUNC = re.compile(r"url\s*\(", re.IGNORECASE)
_URL_LOCAL = re.compile(r"url\s*\(\s*['\"]?\s*#", re.IGNORECASE)
_SVG_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]*$")
_SVG_STYLE_VALUE = re.compile(r"^[-+.,\s0-9a-zA-Z%#()'\"]*$")


def _url_safe(value: str) -> bool:
    """True when every url( in the value is url(#...). A backslash is refused
    outright: a CSS escape (`u\\72l(`) would hide a url( from the check."""
    if "\\" in value:
        return False
    return len(_URL_FUNC.findall(value)) == len(_URL_LOCAL.findall(value))


def _svg_style(value: str) -> str:
    """Presentation declarations only: no position, no layout, no fetch."""
    kept = []
    for declaration in (value or "").split(";"):
        if ":" not in declaration:
            continue
        name, _, raw = declaration.partition(":")
        name, raw = name.strip().lower(), raw.strip()
        if name not in SVG_PRESENTATION or not raw:
            continue
        if not _SVG_STYLE_VALUE.match(raw) or not _url_safe(raw):
            continue
        kept.append(f"{name}:{raw}")
    return ";".join(kept)

# SVG is case-sensitive and `html.parser` lowercases every tag and attribute
# name it reports. Emitting what it hands back would turn `viewBox` into
# `viewbox`, which SVG ignores — so the graphic loses its coordinate system and
# renders at the wrong scale, or not at all. These are the camelCase names that
# actually matter; anything not listed is genuinely lowercase in SVG.
_SVG_CANONICAL = {
    name.lower(): name
    for name in (
        # elements
        "linearGradient", "radialGradient", "clipPath", "textPath", "solidColor",
        "animateMotion", "animateTransform", "feGaussianBlur", "feColorMatrix",
        "feComposite", "feBlend", "feFlood", "feImage", "feMerge", "feMergeNode",
        "feMorphology", "feOffset", "feTile", "feTurbulence", "feDropShadow",
        "feDiffuseLighting", "feSpecularLighting", "feDistantLight",
        "fePointLight", "feSpotLight", "feConvolveMatrix",
        "feComponentTransfer", "feFuncR", "feFuncG", "feFuncB", "feFuncA",
        "feDisplacementMap",
        # attributes
        "viewBox", "preserveAspectRatio", "gradientUnits", "gradientTransform",
        "patternUnits", "patternContentUnits", "patternTransform",
        "clipPathUnits", "clipRule", "maskUnits", "maskContentUnits",
        "markerWidth", "markerHeight", "markerUnits", "refX", "refY",
        "spreadMethod", "stdDeviation", "baseFrequency", "numOctaves",
        "startOffset", "textLength", "lengthAdjust", "pathLength",
        "attributeName", "attributeType", "calcMode", "keyTimes", "keySplines",
        "keyPoints", "repeatCount", "repeatDur", "primitiveUnits",
        "filterUnits", "filterRes", "xChannelSelector", "yChannelSelector",
        "surfaceScale", "specularConstant", "specularExponent",
        "diffuseConstant", "kernelMatrix", "kernelUnitLength", "tableValues",
        "limitingConeAngle", "pointsAtX", "pointsAtY", "pointsAtZ",
        "systemLanguage", "requiredFeatures", "requiredExtensions",
        "baseProfile", "zoomAndPan", "stitchTiles", "edgeMode", "preserveAlpha",
        "targetX", "targetY",
    )
}


def _canon(name: str) -> str:
    return _SVG_CANONICAL.get(name, name)


class _SvgCleaner(HTMLParser):
    """An allowlist: drawing elements and their attributes, nothing else.

    A forbidden element goes **with its subtree**. Skipping is tracked as (tag
    name, depth) rather than a bare counter: a counter that any end tag
    decrements lets `<script><a></a>payload</script>` resume emitting at
    `payload`. An end tag of an element still open above the dropped one ends
    the drop too, so an unclosed `<p>` cannot swallow the closing `</svg>`.

    Output is balanced: an end tag closes only an element this cleaner opened
    (stray `</div>`s that would close the slide's own containers go), and
    whatever is still open at the end is closed. Nothing is kept outside an
    `<svg>` root.
    """

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self._open: list[str] = []
        self._skip_tag: str | None = None
        self._skip_depth = 0

    @property
    def _suppress(self) -> bool:
        return self._skip_tag is not None

    def _begin_skip(self, tag: str) -> None:
        self._skip_tag, self._skip_depth = tag, 1

    def _attrs(self, tag: str, attrs) -> str | None:
        """The kept attributes, or None when the whole element must go."""
        is_animate = tag in SVG_ANIMATE_TAGS
        parts = []
        for name, value in attrs:
            name = (name or "").lower()
            value = value or ""
            if name not in SVG_ATTRS:
                continue                    # every on*, class, data-*, ...
            if is_animate and name == "attributename":
                target = value.strip().lower()
                if target not in SVG_ATTRS or target in _ANIMATE_FORBIDDEN:
                    return None             # drop the whole element
            if name in ("href", "xlink:href"):
                # Only same-document references. An external one is both a
                # tracking beacon and, via <use>, a script-injection vector.
                if not value.strip().startswith("#") or not _url_safe(value):
                    continue
            elif name == "id":
                if not _SVG_ID.match(value):
                    continue
            elif name == "style":
                value = _svg_style(value)
                if not value:
                    continue
            elif not _url_safe(value):
                continue
            parts.append(f' {_canon(name)}="{escape(value, quote=True)}"')
        return "".join(parts)

    def _start(self, tag: str, attrs, *, closed: bool) -> None:
        tag = tag.lower()
        if self._suppress:
            if tag == self._skip_tag and not closed:
                self._skip_depth += 1          # same tag nested inside itself
            return
        if tag in SVG_UNWRAP and self._open:
            return                             # the children stay
        rendered = None
        if tag in SVG_ELEMENTS and (self._open or tag == "svg"):
            rendered = self._attrs(tag, attrs)
        if rendered is None:
            if not closed and tag not in _NEVER_CLOSED:
                self._begin_skip(tag)
            return
        if closed:
            self.out.append(f"<{_canon(tag)}{rendered}/>")
        else:
            self.out.append(f"<{_canon(tag)}{rendered}>")
            self._open.append(tag)

    def handle_starttag(self, tag, attrs):
        self._start(tag, attrs, closed=False)

    def handle_startendtag(self, tag, attrs):
        self._start(tag, attrs, closed=True)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._suppress:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if self._skip_depth <= 0:
                    self._skip_tag, self._skip_depth = None, 0
                return
            if tag not in self._open:
                return
            self._skip_tag, self._skip_depth = None, 0   # an ancestor closes
        if tag not in self._open:
            return                              # stray close tag; ignore it
        while self._open:
            open_tag = self._open.pop()
            self.out.append(f"</{_canon(open_tag)}>")
            if open_tag == tag:
                break

    def _text(self, text: str) -> None:
        if self._open and not self._suppress:
            self.out.append(text)

    def handle_data(self, data):
        self._text(_esc_text(data))

    def handle_entityref(self, name):
        self._text(f"&{name};")

    def handle_charref(self, name):
        self._text(f"&#{name};")

    def handle_comment(self, data):
        pass

    def handle_decl(self, decl):
        pass                                          # <!DOCTYPE ...>

    def handle_pi(self, data):
        pass                                          # <?xml ... ?>

    def unknown_decl(self, data):
        pass                                          # <![CDATA[ ... ]]>

    def value(self) -> str:
        while self._open:
            self.out.append(f"</{_canon(self._open.pop())}>")
        return "".join(self.out).strip()


def sanitize_svg(markup: str) -> str:
    """Inline SVG reduced to drawing elements and presentation attributes."""
    cleaner = _SvgCleaner()
    cleaner.feed(markup or "")
    cleaner.close()
    return cleaner.value()


# ---------------------------------------------------------------------------
# KaTeX
# ---------------------------------------------------------------------------

# The rendered markup for a formula is cached in the document so the PDF — which
# runs no JavaScript — can show it. It is produced by KaTeX in a browser, which
# means it arrives from the client and is exactly as untrusted as anything else
# a browser sends.
#
# KaTeX's output is spans with `katex-` classes and a handful of inline metrics,
# plus an accessible MathML tree. Nothing in it needs a URL, an event handler or
# a positioning property, so the allowlist can be tight.

KATEX_TAGS = {
    "span", "svg", "path", "line", "g",
    # MathML, which KaTeX emits alongside the visual output for screen readers.
    "math", "semantics", "annotation", "mrow", "mi", "mo", "mn", "ms", "mtext",
    "msup", "msub", "msubsup", "mfrac", "msqrt", "mroot", "munder", "mover",
    "munderover", "mtable", "mtr", "mtd", "mspace", "mpadded", "mphantom",
    "mstyle", "menclose", "mmultiscripts", "none", "mprescripts",
}

# Only the metrics KaTeX actually sets. No position, no transform beyond the
# SVG stretchy glyphs, and nothing that can move content out of its block.
KATEX_STYLE_PROPS = {
    "height", "width", "min-width", "vertical-align", "top", "bottom", "left",
    "margin", "margin-left", "margin-right", "margin-top", "margin-bottom",
    "padding-left", "padding-right", "border-bottom-width", "border-top-width",
    "font-size", "font-family", "line-height", "color", "position",
}

# `position` is allowed only for the two values KaTeX uses to stack glyphs.
_KATEX_POSITION_OK = {"relative", "absolute"}

_STYLE_VALUE = re.compile(r"^[-+.,\s0-9a-zA-Z%()#]*$")


def _katex_style(value: str) -> str:
    """Keep the metric declarations, drop everything else."""
    kept = []
    for declaration in (value or "").split(";"):
        if ":" not in declaration:
            continue
        name, _, raw = declaration.partition(":")
        name, raw = name.strip().lower(), raw.strip()
        if name not in KATEX_STYLE_PROPS or not raw:
            continue
        if not _STYLE_VALUE.match(raw):
            continue                          # url(), expression(), anything odd
        if name == "position" and raw.lower() not in _KATEX_POSITION_OK:
            continue
        kept.append(f"{name}:{raw}")
    return ";".join(kept)


class _KatexCleaner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self._skip_tag: str | None = None
        self._skip_depth = 0

    @property
    def _suppress(self) -> bool:
        return self._skip_tag is not None

    def _attrs(self, attrs) -> str:
        parts = []
        for name, value in attrs:
            name = (name or "").lower()
            value = value or ""
            if name == "class":
                # KaTeX's own classes only. A class from anywhere else could
                # borrow styling from the surrounding page.
                keep = [c for c in value.split()
                        if c == "katex" or c.startswith("katex-")
                        or c in ("base", "strut", "mord", "mrel", "mbin", "mopen",
                                 "mclose", "mpunct", "minner", "mop", "vlist",
                                 "vlist-r", "vlist-s", "vlist-t", "vlist-t2",
                                 "frac-line", "sizing", "delimsizing", "mfrac",
                                 "sqrt", "accent", "op-symbol", "mspace",
                                 "hide-tail", "stretchy", "svg-align",
                                 "mathnormal", "mathdefault", "mathrm", "mathit",
                                 "mathbf", "mathsf", "mathtt", "mathcal",
                                 "mathfrak", "mathbb", "mathscr", "text",
                                 "textbf", "textit", "boxpad", "fbox", "cancel-pad")
                        or c.startswith(("size", "delim-size", "reset-size",
                                         "col-align", "arraycolsep", "mtight"))]
                if keep:
                    parts.append(f' class="{escape(" ".join(keep), quote=True)}"')
            elif name == "style":
                cleaned = _katex_style(value)
                if cleaned:
                    parts.append(f' style="{escape(cleaned, quote=True)}"')
            elif name in ("aria-hidden", "aria-label", "role", "viewbox",
                          "preserveaspectratio", "d", "xmlns", "encoding",
                          "displaystyle", "scriptlevel", "mathvariant",
                          "stretchy", "fence", "separator", "width", "height",
                          "x", "y", "x1", "x2", "y1", "y2", "fill", "stroke"):
                parts.append(f' {_canon(name)}="{escape(value, quote=True)}"')
            # everything else — every on*, every href, every id — is dropped
        return "".join(parts)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self._suppress:
            if tag == self._skip_tag:
                self._skip_depth += 1
            return
        if tag not in KATEX_TAGS:
            self._skip_tag, self._skip_depth = tag, 1
            return
        self.out.append(f"<{_canon(tag)}{self._attrs(attrs)}>")

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self._suppress or tag not in KATEX_TAGS:
            return
        self.out.append(f"<{_canon(tag)}{self._attrs(attrs)}/>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._suppress:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if self._skip_depth <= 0:
                    self._skip_tag, self._skip_depth = None, 0
            return
        if tag not in KATEX_TAGS:
            return
        self.out.append(f"</{_canon(tag)}>")

    def handle_data(self, data):
        if not self._suppress:
            self.out.append(_esc_text(data))

    def handle_entityref(self, name):
        if not self._suppress:
            self.out.append(f"&{name};")

    def handle_charref(self, name):
        if not self._suppress:
            self.out.append(f"&#{name};")

    def handle_comment(self, data):
        pass

    def value(self) -> str:
        return "".join(self.out).strip()


def sanitize_katex(markup: str) -> str:
    """A cached KaTeX rendering, with everything it does not need removed.

    Returns "" if nothing survives, which the renderer treats as "no cached
    render" and falls back to showing the LaTeX source — never a blank.
    """
    if not (markup or "").strip():
        return ""
    cleaner = _KatexCleaner()
    cleaner.feed(markup)
    cleaner.close()
    return cleaner.value()
