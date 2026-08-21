"""The deck as a PDF, rendered from the same stylesheet as everything else.

Same shape as ``toto.notarius.render``: WeasyPrint is imported lazily and gated
by ``BUILD_WEASYPRINT``, because the wheel is a pip layer in the image rather
than a base requirement — so a host that has not built with it must get a
sentence explaining that, not an ImportError traceback.

What makes this worth doing rather than pointing people at reveal's print view:
the input is ``slide.css``, the same file the editor canvas and the player load.
The PDF is therefore the third surface of one definition of what a slide looks
like, not a fourth thing that resembles the other three.
"""

from __future__ import annotations

from pathlib import Path

from django.template.loader import render_to_string

SLIDE_CSS = Path(__file__).resolve().parent / "static" / "memo" / "slide.css"


def _katex_assets():
    """KaTeX's stylesheet, and the directory its fonts sit in.

    Returns ``(css, base_dir)`` or ``("", None)``.

    KaTeX lives in ``core/static/vendor/``, which is gitignored and downloaded
    at image build — so on a machine where ``download_vendor.py`` has not run it
    is simply absent. That is a normal state, not an error: the formula blocks
    fall back to their LaTeX source, which is readable, rather than to boxes.

    The base directory matters as much as the CSS. `katex.min.css` references
    its fonts with relative ``url(fonts/…)``, and WeasyPrint resolves those
    against the document's base_url — point it anywhere else and every glyph in
    every formula comes out as a blank box.
    """
    from django.contrib.staticfiles import finders

    found = finders.find("vendor/katex/katex.min.css")
    if not found:
        return "", None
    path = Path(found)
    if not (path.parent / "fonts").is_dir():
        # Stylesheet without its fonts is worse than neither: it would render
        # every glyph as a box rather than falling back to the source.
        return "", None
    try:
        return path.read_text(encoding="utf-8"), path.parent
    except OSError:
        return "", None


class PdfUnavailable(RuntimeError):
    """WeasyPrint is not installed on this deployment."""


def is_available() -> bool:
    try:
        import weasyprint            # noqa: F401
    except Exception:                # noqa: BLE001 — ImportError or a missing native lib
        return False
    return True


def build_html(presentation):
    """The deck as one printable document. Returns ``(html, katex_dir)``.

    Separated from rendering because the two now happen in different places:
    the HTML is built here, where the presentation, its theme and the template
    engine are, and the PDF may be produced in a Compute Gear where none of
    those exist.
    """
    # Inlined rather than linked: WeasyPrint would otherwise have to resolve a
    # hashed static URL over HTTP against a server that may not be reachable
    # from inside the container.
    try:
        css = SLIDE_CSS.read_text(encoding="utf-8")
    except OSError:
        css = ""

    katex_css, katex_dir = _katex_assets()
    html = render_to_string("memo/print.html", {
        "presentation": presentation,
        "theme": presentation.theme,
        "font": presentation.font,
        "slide_css": css,
        "katex_css": katex_css,
    })
    return html, katex_dir


def katex_inputs(katex_dir) -> dict:
    """KaTeX's fonts, as ``{staged path: bytes}``, for a render elsewhere.

    ``katex.min.css`` references its fonts with relative ``url(fonts/…)``. On
    this host that resolved against a directory on disk; in a runner the fonts
    have to TRAVEL, or every glyph in every formula comes out as a blank box —
    the same failure ``_katex_assets`` already warns about, one machine
    further away.
    """
    if not katex_dir:
        return {}
    fonts = Path(katex_dir) / "fonts"
    if not fonts.is_dir():
        return {}
    staged = {}
    for path in sorted(fonts.iterdir()):
        # woff2 alone: KaTeX ships three formats of every face, and a browser
        # would pick one — WeasyPrint takes the first it can read, so sending
        # the other two triples the payload for nothing.
        if path.is_file() and path.suffix == ".woff2":
            staged[f"fonts/{path.name}"] = path.read_bytes()
    return staged


def render(presentation) -> bytes:
    """One page per slide, at the same 1280x720 geometry as the player.

    **In this process.** The ordinary path on a host with Compute Gears is
    :func:`render_in_gear`; this is what a host without them uses, and it is
    why Irena — which deliberately has no Anastasia — keeps working unchanged.
    """
    try:
        from weasyprint import HTML  # lazy: gated by BUILD_WEASYPRINT
    except Exception as exc:         # noqa: BLE001
        raise PdfUnavailable(
            "PDF export needs WeasyPrint, which is not installed on this "
            "deployment. Build the image with BUILD_WEASYPRINT=1, or use "
            "Present and print from the browser."
        ) from exc

    html, katex_dir = build_html(presentation)
    # base_url is what lets katex.min.css find its fonts; there is nothing else
    # relative in the document, so pointing it at the katex directory is safe.
    base_url = str(katex_dir) + "/" if katex_dir else None
    return HTML(string=html, base_url=base_url).write_pdf()


def render_in_gear(presentation, *, lease, user) -> bytes:
    """Build the HTML here, produce the PDF in the user's Compute Gear.

    Same cut every migrated caller makes: what needs the database and the
    template engine stays; what needs a heavy library goes. The runner receives
    the document plus its fonts and nothing else.
    """
    from toto.anastasia import jobs

    html, katex_dir = build_html(presentation)
    inputs = {"input.html": html}
    inputs.update(katex_inputs(katex_dir))

    result = jobs.run(
        lease=lease, operation="render_pdf", inputs=inputs,
        subject_label="memo.Presentation", subject_id=presentation.pk,
        requested_by=user)
    pdf = result["outputs"].get("output.pdf")
    if not pdf:
        raise PdfUnavailable(
            "The Compute Gear finished but produced no PDF.")
    return pdf


def gear_for(user):
    """The Gear to export in, or None to render here.

    None on a host with no Anastasia — which is the whole of Irena — so this
    module keeps one code path for "there is nowhere else to run it".
    """
    from django.apps import apps

    if not apps.is_installed("toto.anastasia"):
        return None
    from toto.anastasia import jobs

    try:
        return jobs.require_gear(user)
    except jobs.NoGear:
        if is_available():
            return None          # fall back to rendering here
        raise
