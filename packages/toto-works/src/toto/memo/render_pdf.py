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


class PdfUnavailable(RuntimeError):
    """WeasyPrint is not installed on this deployment."""


def is_available() -> bool:
    try:
        import weasyprint            # noqa: F401
    except Exception:                # noqa: BLE001 — ImportError or a missing native lib
        return False
    return True


def render(presentation) -> bytes:
    """One page per slide, at the same 1280x720 geometry as the player."""
    try:
        from weasyprint import HTML  # lazy: gated by BUILD_WEASYPRINT
    except Exception as exc:         # noqa: BLE001
        raise PdfUnavailable(
            "PDF export needs WeasyPrint, which is not installed on this "
            "deployment. Build the image with BUILD_WEASYPRINT=1, or use "
            "Present and print from the browser."
        ) from exc

    # Inlined rather than linked: WeasyPrint would otherwise have to resolve a
    # hashed static URL over HTTP against a server that may not be reachable
    # from inside the container.
    try:
        css = SLIDE_CSS.read_text(encoding="utf-8")
    except OSError:
        css = ""

    html = render_to_string("memo/print.html", {
        "presentation": presentation,
        "theme": presentation.theme,
        "font": presentation.font,
        "slide_css": css,
    })
    return HTML(string=html).write_pdf()
