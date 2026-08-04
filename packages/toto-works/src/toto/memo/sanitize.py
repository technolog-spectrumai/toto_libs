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

SVG_DROP_TAGS = {
    "script", "foreignobject", "handler", "listener",
    "iframe", "object", "embed", "audio", "video",
}

# `<animate attributeName="href">` rewrites a link target after load, so it is
# the one animation element that has to go. The rest are legitimate.
SVG_ANIMATE_TAGS = {"animate", "animatetransform", "animatemotion", "set"}

_URL_IN_STYLE = re.compile(r"url\(\s*['\"]?\s*([a-z][a-z0-9+.-]*)\s*:", re.IGNORECASE)

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
        "baseProfile", "zoomAndPan",
    )
}


def _canon(name: str) -> str:
    return _SVG_CANONICAL.get(name, name)


class _SvgCleaner(HTMLParser):
    """Drops a forbidden element **with its subtree**.

    Skipping is tracked as (tag name, depth) rather than a bare counter: a
    counter that any end tag decrements lets `<script><a></a>payload</script>`
    resume emitting at `payload`, which defeats the whole exercise.
    """

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self._skip_tag: str | None = None
        self._skip_depth = 0

    @property
    def _suppress(self) -> bool:
        return self._skip_tag is not None

    def _begin_skip(self, tag: str) -> None:
        self._skip_tag, self._skip_depth = tag, 1

    def _attrs(self, attrs, *, is_animate: bool) -> str | None:
        parts = []
        for name, value in attrs:
            name = (name or "").lower()
            value = value or ""
            if name.startswith("on"):
                continue                              # every event handler
            if is_animate and name == "attributename" \
                    and value.strip().lower() in ("href", "xlink:href"):
                return None                           # drop the whole element
            if name in ("href", "xlink:href", "src"):
                # Only same-document references. An external one is both a
                # tracking beacon and, via <use>, a script-injection vector.
                if not value.strip().startswith("#"):
                    continue
            if name == "style" and _URL_IN_STYLE.search(value):
                scheme = _URL_IN_STYLE.search(value).group(1).lower()
                if scheme not in ("http", "https", "data"):
                    continue
            parts.append(f' {_canon(name)}="{escape(value, quote=True)}"')
        return "".join(parts)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if self._suppress:
            if tag == self._skip_tag:
                self._skip_depth += 1          # same tag nested inside itself
            return
        if tag in SVG_DROP_TAGS:
            self._begin_skip(tag)
            return
        rendered = self._attrs(attrs, is_animate=tag in SVG_ANIMATE_TAGS)
        if rendered is None:
            self._begin_skip(tag)
            return
        self.out.append(f"<{_canon(tag)}{rendered}>")

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self._suppress or tag in SVG_DROP_TAGS:
            return
        rendered = self._attrs(attrs, is_animate=tag in SVG_ANIMATE_TAGS)
        if rendered is None:
            return
        self.out.append(f"<{_canon(tag)}{rendered}/>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._suppress:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if self._skip_depth <= 0:
                    self._skip_tag, self._skip_depth = None, 0
            return
        if tag in SVG_DROP_TAGS:
            return                              # stray close tag; ignore it
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

    def handle_decl(self, decl):
        pass                                          # <!DOCTYPE ...>

    def handle_pi(self, data):
        pass                                          # <?xml ... ?>

    def value(self) -> str:
        return "".join(self.out).strip()


def sanitize_svg(markup: str) -> str:
    """Inline SVG with scripts, event handlers and external refs removed."""
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
