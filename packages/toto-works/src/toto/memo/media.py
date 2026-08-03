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
import urllib.parse
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


_DATA_URI = re.compile(r"^data:([^;,]*)(;base64)?,(.*)$", re.DOTALL)

# Enough to name a file. Not a full registry — anything unrecognised becomes
# .bin, which still round-trips because the manifest records the payload as-is.
_EXT_FOR_MIME = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
    "image/webp": ".webp", "image/svg+xml": ".svg", "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}


def data_uri_to_bytes(uri: str) -> "tuple[bytes, str, str]":
    """``(raw, mime, extension)`` for a ``data:`` URI.

    The inverse of :func:`image_bytes_to_data_uri`, and what lets the ZIP export
    write a real ``.png`` next to the XML instead of two megabytes of base64
    that nobody can open.
    """
    match = _DATA_URI.match((uri or "").strip())
    if not match:
        return b"", "", ".bin"
    mime = (match.group(1) or "application/octet-stream").strip()
    payload = match.group(3) or ""
    if match.group(2):
        try:
            raw = base64.b64decode(payload, validate=False)
        except Exception:                              # noqa: BLE001
            raw = b""
    else:
        raw = urllib.parse.unquote_to_bytes(payload)
    return raw, mime, _EXT_FOR_MIME.get(mime, ".bin")


def bytes_to_data_uri(raw: bytes, mime: str) -> str:
    """Straight base64, with NO resize — the inverse of the export path.

    Deliberately not `image_bytes_to_data_uri`: re-importing an archive must
    return the bytes that were exported, and running them through the 640px
    thumbnailer again would shrink a picture a little more on every round trip.
    """
    return ("data:" + (mime or "application/octet-stream")
            + ";base64," + base64.b64encode(raw).decode("ascii"))


def clean_svg_markup(text: str) -> str:
    """Inline SVG with everything executable removed.

    This used to be four regexes that stripped the XML prolog, the doctype and
    ``<script>``. A regex cannot see attributes, so ``<svg onload="...">`` went
    straight through into the player — as did ``<a href="javascript:...">`` and
    an external ``<use href="//host/x#y">``, which is a script-injection vector
    in its own right. The editor's client-side path did not even strip
    ``<script>``.

    It now delegates to :mod:`toto.memo.sanitize`, which walks the markup with a
    real parser. Kept as a function here because the vault picker, the upload
    endpoint and the format layer all call it, and one name is easier to keep
    right than three.
    """
    from .sanitize import sanitize_svg

    return sanitize_svg(text)
