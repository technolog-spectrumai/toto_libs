"""The document as a PDF, with real page furniture.

Same gating shape as ``toto.notarius.render`` and ``toto.memo.render_pdf``:
WeasyPrint is imported lazily and gated by ``BUILD_WEASYPRINT``, because the
wheel is a pip layer in the image rather than a base requirement — a host that
has not built with it must get a sentence explaining that, not an ImportError.

What is new here is what comes out. Nothing else in this repo produces page
furniture: between them ``notarius/render.py`` and ``memo/render_pdf.py`` hold
the only two ``@page`` rules in the tree, and neither declares a margin box, a
page counter or a table of contents. This does all three, because a document
that reads as *finished* rather than merely printed is most of the point of
writing one:

* a running header carrying the document's own title, via ``string-set`` and
  ``@top-center`` — and suppressed on the cover by ``@page :first``;
* page numbers from ``counter(page)`` in ``@bottom-center``;
* a contents page whose numbers are **real**, resolved after layout by
  ``target-counter()``. That is the thing a browser cannot do and a print engine
  can: the number is the page the target actually landed on.

The page geometry comes from ``document.css`` — the same file the writer and the
reader load — so the PDF is the third surface of one definition rather than a
fourth thing that resembles the other three.
"""

from __future__ import annotations

from pathlib import Path

from django.template.loader import render_to_string

DOCUMENT_CSS = Path(__file__).resolve().parent / "static" / "cyprian" / "document.css"


class PdfUnavailable(RuntimeError):
    """WeasyPrint is not installed on this deployment."""


def is_available() -> bool:
    try:
        import weasyprint            # noqa: F401
    except Exception:                # noqa: BLE001 — ImportError or a missing native lib
        return False
    return True


def _katex_assets():
    """KaTeX's stylesheet and the directory its fonts sit in, or ``("", None)``.

    KaTeX lives in ``core/static/vendor/``, which is gitignored and downloaded at
    image build — so on a machine where ``download_vendor.py`` has not run it is
    simply absent. That is a normal state, not an error: formula blocks fall back
    to their LaTeX source, which is readable.

    Both halves must be present. A stylesheet without its fonts renders every
    glyph as an empty box, which is strictly worse than showing the source.
    """
    from django.contrib.staticfiles import finders

    found = finders.find("vendor/katex/katex.min.css")
    if not found:
        return "", None
    path = Path(found)
    if not (path.parent / "fonts").is_dir():
        return "", None
    try:
        return path.read_text(encoding="utf-8"), path.parent
    except OSError:
        return "", None


def document_css() -> str:
    """The one stylesheet, as text — for anything that has to inline it."""
    try:
        return DOCUMENT_CSS.read_text(encoding="utf-8")
    except OSError:
        return ""


def katex_css() -> str:
    """KaTeX's stylesheet, or "" when it was never vendored."""
    return _katex_assets()[0]


def render(document, watermark: str = "", watermark_image: str = "") -> bytes:
    """The document as PDF bytes.

    ``watermark`` is plain text drawn diagonally across every page — DRAFT,
    CONFIDENTIAL, a recipient's name. Text, never markup: it goes through the
    template autoescaped, and a watermark is a stamp, not a canvas.

    ``watermark_image`` is the same idea as a picture: a ``data:image/`` URI
    (the only form the vault picker produces, so WeasyPrint never fetches
    anything), drawn faint behind the text of every page. When both are given
    the image wins — one stamp per page.
    """
    try:
        from weasyprint import HTML  # lazy: gated by BUILD_WEASYPRINT
    except Exception as exc:         # noqa: BLE001
        raise PdfUnavailable(
            "PDF export needs WeasyPrint, which is not installed on this "
            "deployment. Build the image with BUILD_WEASYPRINT=1."
        ) from exc

    # Inlined rather than linked: WeasyPrint would otherwise have to resolve a
    # hashed static URL over HTTP against a server that may not be reachable
    # from inside the container.
    try:
        css = DOCUMENT_CSS.read_text(encoding="utf-8")
    except OSError:
        css = ""

    katex_css, katex_dir = _katex_assets()
    css = _page_rule(document) + css

    html = render_to_string("cyprian/print.html", {
        "document": document,
        "watermark": watermark if not watermark_image else "",
        "watermark_image": watermark_image,
        "document_css": css,
        "katex_css": katex_css,
    })
    # base_url is what lets katex.min.css find its fonts; nothing else in the
    # document is relative, so pointing it at the katex directory is safe.
    base_url = str(katex_dir) + "/" if katex_dir else None
    return HTML(string=html, base_url=base_url).write_pdf()


# A4 only. `page` is still parsed from older files and ignored — see
# document_format's module docstring for why the choice went away.
_PAGE_SIZE = "A4"
_MARGINS = {
    "normal": "25mm 20mm",
    "narrow": "12mm 12mm",
    "wide":   "35mm 35mm",
}


def _page_rule(document) -> str:
    """The `@page` block: size, margins, and the furniture in the margin boxes.

    Restated in millimetres here because the screen stylesheet is in CSS pixels
    at 96 dpi, and a print engine should be told the real size rather than
    asked to convert one.
    """
    margin = _MARGINS.get(document.margins, _MARGINS["normal"])
    return (
        "@page {"
        f"  size: {_PAGE_SIZE};"
        f"  margin: {margin};"
        '  @top-center { content: string(cy-doc-title);'
        "    font-size: 9pt; color: #666; }"
        '  @bottom-center { content: "— " counter(page) " —";'
        "    font-size: 9pt; color: #666; }"
        "}"
        "@page :first { @top-center { content: none; }"
        "               @bottom-center { content: none; } }"
    )
