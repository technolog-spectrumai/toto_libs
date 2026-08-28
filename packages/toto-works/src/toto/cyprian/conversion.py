"""HTML ⇄ CTML, and an honest account of what each direction costs.

The two formats are not the same thing and this module refuses to pretend they
are. What it does instead is **count while converting**, so the report handed to
the person is a by-product of the conversion itself and cannot drift away from
what the code actually did.

WHY THE ASYMMETRY. A CTML document's body IS HTML — `ctml.dumps` writes one
`<content>` element wrapping a single CDATA block of sanitised markup, and there
is no per-node XML anywhere in the format. So:

* **CTML → HTML is close to lossless.** The body carries over verbatim; the
  document's title, font and margins become a `<title>` and one small `<style>`
  block. What has nowhere to go is the `toc` and `cover` flags — HTML has no
  concept of either.
* **HTML → CTML is genuinely lossy**, because a whole page is more than one rich
  body. Everything outside `<body>` goes, and inside it the sanitiser's
  allowlist decides the rest.

WHAT IS NOT DONE HERE. Neither direction ever touches the file it was given.
Both callers create a NEW file beside the source. That is not politeness: a
version is captured AFTER a write, so an in-place conversion would replace the
bytes and leave no version holding the original.
"""

from __future__ import annotations

import dataclasses
import re

from django.utils.translation import gettext as _

from . import ctml, from_html


@dataclasses.dataclass(frozen=True)
class Loss:
    """One kind of thing that did not survive, and how much of it there was."""

    #: A stable machine name, so a test can assert on the KIND rather than on
    #: the sentence — which is translated and may be reworded.
    kind: str
    count: int
    message: str


@dataclasses.dataclass(frozen=True)
class Report:
    """What a conversion produced, and what it cost.

    `losses` is itemised and countable on purpose. "This conversion is lossy"
    is not something anybody can act on; "3 <script> blocks removed, inline
    styles dropped on 12 elements" is.
    """

    text: str
    losses: tuple = ()

    @property
    def lossless(self) -> bool:
        return not self.losses

    @property
    def total(self) -> int:
        return sum(loss.count for loss in self.losses)


def _count(pattern: re.Pattern, html: str) -> int:
    return len(pattern.findall(html or ""))


# What a whole page carries that one rich body cannot hold. Each pattern is
# paired with the sentence shown for it, and both are used to BUILD the report
# rather than to predict it.
_HEAD = re.compile(r"<head\b[^>]*>.*?</head\s*>", re.I | re.S)
_STYLE_BLOCK = re.compile(r"<style\b[^>]*>.*?</style\s*>", re.I | re.S)
_SCRIPT_BLOCK = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.I | re.S)
_STYLESHEET = re.compile(r"<link\b[^>]*\brel\s*=\s*[\"']?stylesheet", re.I)
_FORM = re.compile(r"<form\b[^>]*>", re.I)
_FRAME = re.compile(r"<(iframe|object|embed|frameset)\b[^>]*>", re.I)
_INLINE_STYLE = re.compile(r"\sstyle\s*=", re.I)
_CLASS_OR_ID = re.compile(r"\s(?:class|id)\s*=", re.I)
_HANDLER = re.compile(r"\son[a-z]+\s*=", re.I)


def html_to_ctml(vault_file, html: str, *, title: str = "") -> Report:
    """A whole HTML page as a CTML document, and what the page lost.

    Counts against the ORIGINAL page, then converts. Doing it in that order is
    what keeps the report truthful: the numbers describe what was in the file
    somebody handed over, not what survived a first pass.
    """
    html = html or ""
    losses = []

    def note(kind, count, message):
        if count:
            losses.append(Loss(kind=kind, count=count, message=message))

    # Outside the body. `body_of` discards all of it.
    note("head", _count(_HEAD, html),
         _("The page head was dropped — the document keeps a title of its own."))
    note("stylesheet", _count(_STYLESHEET, html),
         _("Linked stylesheets were dropped; a document has its own font and "
           "margins."))
    note("style-block", _count(_STYLE_BLOCK, html),
         _("<style> blocks were dropped."))
    note("script", _count(_SCRIPT_BLOCK, html),
         _("<script> blocks were removed."))

    # Inside the body. The sanitiser's allowlist decides these.
    note("form", _count(_FORM, html),
         _("Forms were flattened to the text inside them."))
    note("frame", _count(_FRAME, html),
         _("Embedded frames and objects were removed."))
    note("inline-style", _count(_INLINE_STYLE, html),
         _("Inline style rules were dropped."))
    note("class-or-id", _count(_CLASS_OR_ID, html),
         _("class and id attributes were dropped."))
    note("handler", _count(_HANDLER, html),
         _("Event handlers were removed."))

    document = ctml.new_document(
        title or from_html.title_of(html, fallback=getattr(vault_file, "title", "")))
    # `from_dict` is the sanitisation choke point — the same one a save goes
    # through — so the body that lands here is exactly the body the writer
    # would have produced. The report above described this, before it happened.
    document = ctml.Document.from_dict({
        **document.to_dict(),
        "content": from_html.body_of(html),
    })
    document.toc = False
    return Report(text=ctml.dumps(document), losses=tuple(losses))


def ctml_to_html(document) -> Report:
    """A CTML document as a standalone HTML page, and what the document lost.

    Close to lossless, and the loop is deliberately closed the same way
    `from_html.page_of` closed it: the body is already sanitised on the way IN
    (`Document.from_dict`), so writing it into a page is a fact rather than a
    hope.
    """
    losses = []
    if getattr(document, "toc", False):
        losses.append(Loss(
            kind="toc", count=1,
            message=_("The contents page is not part of an HTML file.")))
    if getattr(document, "cover", False):
        losses.append(Loss(
            kind="cover", count=1,
            message=_("The cover page is not part of an HTML file.")))

    title = (document.title or "").strip()
    meta = "".join(
        f'<meta name="{_attr(name)}" content="{_attr(value)}">\n'
        for name, value in sorted((document.meta or {}).items()))
    return Report(text=(
        '<!doctype html>\n<html>\n<head>\n<meta charset="utf-8">\n'
        f"<title>{_text(title)}</title>\n{meta}"
        f"<style>\n{_page_style(document)}</style>\n</head>\n<body>\n"
        f"{document.content or ''}\n</body>\n</html>\n"
    ), losses=tuple(losses))


#: The document's own font and margin model, as the one thing a generated page
#: says about presentation. Anything more would be inventing a design the
#: document never carried.
_FONTS = {"serif": "Georgia, 'Times New Roman', serif",
          "sans": "system-ui, -apple-system, 'Segoe UI', sans-serif",
          "mono": "ui-monospace, 'SFMono-Regular', Menlo, monospace"}
_MARGINS = {"narrow": "2rem", "normal": "3rem", "wide": "5rem"}


def _page_style(document) -> str:
    font = _FONTS.get(getattr(document, "font", ""), _FONTS["serif"])
    margin = _MARGINS.get(getattr(document, "margins", ""), _MARGINS["normal"])
    return (f"body {{ font-family: {font}; margin: {margin} auto; "
            f"max-width: 46rem; line-height: 1.5; }}\n")


def _text(value: str) -> str:
    return (value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _attr(value: str) -> str:
    return _text(value).replace('"', "&quot;")
