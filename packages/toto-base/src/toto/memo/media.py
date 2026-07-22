"""Embed helpers for inserting vault images/SVGs into a self-contained ``.pml``.

A presentation is a single self-contained file: raster images are embedded as
base64 ``data:`` URIs and SVGs are inlined verbatim (see
:mod:`toto.memo.presentation_format`). These helpers turn a vault file's bytes
into the same snippet the editor's local-file "Insert image / SVG" path produces,
so a picked vault image lands in the slide body with no external dependency.
"""

from __future__ import annotations

import base64
import io
import re

# Match the editor's client-side resize cap (memo/templates/memo/edit.html).
MAX_DIM = 640


def image_bytes_to_data_uri(raw: bytes, content_type: str = "", max_dim: int = MAX_DIM) -> str:
    """Return a base64 ``data:`` URI for a raster image, downscaled to ``max_dim``.

    PNG sources stay PNG (to keep transparency); everything else is re-encoded as
    JPEG — mirroring the browser editor's ``resizeToDataURL``. If Pillow is missing
    or the bytes can't be decoded, the original bytes are embedded verbatim so the
    slide still gets a valid ``data:`` URI.
    """
    ctype = (content_type or "").lower()
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(raw))
        img.load()
        is_png = "png" in ctype or (img.format or "").lower() == "png"
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim))

        buf = io.BytesIO()
        if is_png:
            img.save(buf, format="PNG", optimize=True)
            mime = "image/png"
        else:
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(buf, format="JPEG", quality=85, optimize=True)
            mime = "image/jpeg"
        data = buf.getvalue()
    except Exception:
        # Undecodable / Pillow absent — embed the original bytes as-is.
        data = raw
        mime = ctype or "application/octet-stream"

    return "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")


def clean_svg_markup(text: str) -> str:
    """Strip the XML prolog/doctype and any ``<script>`` blocks from SVG markup.

    Mirrors the editor's client-side prolog/doctype stripping, and additionally
    removes scripts — a vault SVG may originate from a shared/public file, and the
    inlined markup is rendered live in the viewer.
    """
    text = re.sub(r"<\?xml[\s\S]*?\?>", "", text or "", flags=re.IGNORECASE)
    text = re.sub(r"<!DOCTYPE[\s\S]*?>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<script[\s\S]*?</script\s*>", "", text, flags=re.IGNORECASE)
    return text.strip()
