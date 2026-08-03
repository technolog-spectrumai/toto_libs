"""Export a deck as a ZIP, and read one back.

A deck is normally one self-contained XML file with its pictures inlined as
base64 ``data:`` URIs. That is exactly right for storage — nothing to lose track
of — and exactly wrong for a backup you might want to look inside. A ten-slide
deck is a few megabytes of unreadable base64 on one line.

So the archive turns it inside out::

    my-talk.zip
      presentation.xml          <- readable, diffable, small
      assets/
        01-diagram.png
        02-logo.svg
        03-photo.jpg

The XML is the same v2 document with each image and SVG block's payload replaced
by its relative path. Import puts the bytes back inline, so what lands in the
vault is a single self-contained file again — the format never changes, only how
it travels.

**Import re-inlines the exported bytes without resizing them.** Running them
through the 640px thumbnailer again would shrink every picture a little more on
each export/import cycle, which is a slow way to lose a deck.
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile

from . import presentation_format as pf
from .media import bytes_to_data_uri, data_uri_to_bytes

MANIFEST = "presentation.xml"
ASSET_DIR = "assets"

# One deck, unpacked. Bounded because a ZIP is attacker-supplied: a "zip bomb"
# is a few KB that expands to gigabytes, and the import path has to survive one.
MAX_ENTRIES = 2000
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_ASSET_BYTES = 32 * 1024 * 1024

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(text: str, fallback: str) -> str:
    cleaned = _SAFE_NAME.sub("-", (text or "").strip()).strip("-.")
    return (cleaned or fallback)[:48]


def _is_asset_block(block) -> bool:
    return block.type in ("image", "svg")


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_zip(presentation: pf.Presentation) -> bytes:
    """The deck as a ZIP: readable XML plus its pictures as real files."""
    # A copy — the caller's Presentation is usually the one being rendered, and
    # rewriting its payloads to file paths would blank every image on screen.
    document = pf.Presentation.from_dict(presentation.to_dict())

    assets: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    index = 0

    for slide in document.slides:
        for block in slide.blocks:
            if not _is_asset_block(block) or not block.payload:
                continue
            index += 1
            base = _slug(block.attrs.get("alt", ""), block.id)

            if block.type == "svg":
                raw, ext = block.payload.encode("utf-8"), ".svg"
            else:
                raw, _mime, ext = data_uri_to_bytes(block.payload)
                if not raw:
                    continue            # not a data: URI; leave it alone

            name = f"{index:02d}-{base}{ext}"
            while name in seen:
                index += 1
                name = f"{index:02d}-{base}{ext}"
            seen.add(name)

            assets.append((name, raw))
            # The path goes in an ATTRIBUTE, not the payload. An image block's
            # payload is validated as a data: URI on every load — putting a file
            # path there means the manifest is blanked the moment it is parsed.
            # Unknown attributes round-trip untouched, which is exactly what
            # this needs.
            block.attrs["src"] = posixpath.join(ASSET_DIR, name)
            block.payload = ""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST, pf.dumps(document))
        for name, raw in assets:
            archive.writestr(posixpath.join(ASSET_DIR, name), raw)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

class BundleError(ValueError):
    """The archive is not a deck this can read."""


def _safe_member(name: str) -> str | None:
    """A member path that cannot escape the archive.

    ``zipfile`` will happily hand back ``../../etc/passwd`` or an absolute path;
    nothing is written to disk here, but a traversal name would still let one
    entry masquerade as another slide's asset.
    """
    name = (name or "").replace("\\", "/")
    if name.startswith("/") or ".." in name.split("/"):
        return None
    return name


def import_zip(raw: bytes) -> pf.Presentation:
    """Read an archive back into a self-contained Presentation."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise BundleError("That is not a readable ZIP archive.") from exc

    entries = archive.infolist()
    if len(entries) > MAX_ENTRIES:
        raise BundleError("That archive has too many files in it.")
    if sum(e.file_size for e in entries) > MAX_TOTAL_BYTES:
        raise BundleError("That archive unpacks to too much data.")

    names = {}
    for entry in entries:
        safe = _safe_member(entry.filename)
        if safe is not None:
            names[safe] = entry

    if MANIFEST not in names:
        raise BundleError(
            f"No {MANIFEST} in the archive — is this a presentation export?")

    try:
        document = pf.loads(archive.read(names[MANIFEST]).decode("utf-8"))
    except (UnicodeDecodeError, pf.PresentationParseError) as exc:
        raise BundleError(f"{MANIFEST} could not be read: {exc}") from exc

    for slide in document.slides:
        for block in slide.blocks:
            if not _is_asset_block(block):
                continue
            src = _safe_member(block.attrs.pop("src", "") or "")
            if not src:
                continue                       # already inline, or nothing to do
            entry = names.get(src)
            if entry is None or entry.file_size > MAX_ASSET_BYTES:
                block.payload = ""             # missing asset: an empty block
                continue
            raw_asset = archive.read(entry)
            if block.type == "svg":
                block.payload = raw_asset.decode("utf-8", errors="replace")
            else:
                block.payload = bytes_to_data_uri(raw_asset, _mime_for(src))

    # Back through from_dict so the sanitisers run: an archive is a file
    # somebody handed us, not something this code wrote.
    return pf.Presentation.from_dict(document.to_dict())


_MIME_FOR_EXT = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".tiff": "image/tiff", ".svg": "image/svg+xml",
}


def _mime_for(path: str) -> str:
    dot = path.rfind(".")
    return _MIME_FOR_EXT.get(path[dot:].lower() if dot != -1 else "",
                             "application/octet-stream")
