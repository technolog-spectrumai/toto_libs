"""Taking a chain out of the database in a form somebody else can check.

Two shapes, and the reason for both:

* **one XML document** — the whole chain in reading order, wrapped in a header
  that names the hash algorithm and the format version. Everything a verifier
  needs is inside the file; it does not have to ask this platform anything.
* **a ZIP manifest** — one file per block plus a manifest listing them. This is
  the shape for keeping a chain somewhere that is not a database: files diff,
  files go in a bucket, and a single block can be handed to someone without
  handing them the whole history.

Both carry the algorithm and format version, because a hash is meaningless
without the name of the function that produced it. That is the same reason the
genesis block records them.
"""

from __future__ import annotations

import io
import zipfile
from html import escape

from toto.ledger.models import Ledger
from toto.ledger.services.chain import chain_algorithm, entry_block_xml


def _attr(value) -> str:
    return escape(str(value or ""), quote=True)


def chain_document(ledger: Ledger) -> str:
    """The whole chain as one XML document."""
    algorithm = chain_algorithm(ledger)
    blocks = "".join(
        entry_block_xml(entry)
        for entry in ledger.entries.select_related("ledger").order_by("sequence")
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<ledger uid="{_attr(ledger.uid)}" key="{_attr(ledger.key)}" '
        f'name="{_attr(ledger.name)}" kind="{_attr(ledger.kind)}" '
        f'scope-type="{_attr(ledger.scope_type)}" scope-uid="{_attr(ledger.scope_uid)}" '
        f'algorithm="{_attr(algorithm)}" format="{_attr(ledger.format_version)}">'
        f"<blocks>{blocks}</blocks>"
        "</ledger>"
    )


def manifest_document(ledger: Ledger) -> str:
    """What is in the ZIP, and what each block's hash is."""
    algorithm = chain_algorithm(ledger)
    rows = "".join(
        f'<block sequence="{entry.sequence}" uid="{_attr(entry.uid)}" '
        f'file="blocks/{entry.sequence:06d}.xml" '
        f'hash="{_attr(entry.entry_hash)}" '
        f'previous-hash="{_attr(entry.previous_hash)}" '
        f'signed="{"true" if entry.signature else "false"}"/>'
        for entry in ledger.entries.order_by("sequence")
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<manifest ledger="{_attr(ledger.uid)}" key="{_attr(ledger.key)}" '
        f'algorithm="{_attr(algorithm)}" format="{_attr(ledger.format_version)}" '
        f'blocks="{ledger.entries.count()}">'
        f"{rows}"
        "</manifest>"
    )


def chain_zip(ledger: Ledger) -> bytes:
    """The chain as a ZIP: a manifest, one file per block, and the whole document.

    Deterministic: fixed timestamps and sorted names, so exporting the same
    chain twice produces identical bytes and a diff of two exports shows only
    what actually changed on the chain.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        def write(name: str, text: str):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, text)

        write("manifest.xml", manifest_document(ledger))
        write("ledger.xml", chain_document(ledger))
        for entry in ledger.entries.select_related("ledger").order_by("sequence"):
            write(f"blocks/{entry.sequence:06d}.xml", entry_block_xml(entry))
    return buffer.getvalue()


def export_filename(ledger: Ledger, suffix: str) -> str:
    return f"ledger-{ledger.key}-{ledger.entries.count():06d}.{suffix}"
