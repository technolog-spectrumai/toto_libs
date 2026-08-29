"""CTML is retired: wiki pages become HTML, standalone documents are DELETED.

WHY. The writer's own file format was one container around a body that was
always HTML, and it cost a file type, a parser, a sniffer, an extension, a
conversion UI, a per-type access plugin and a second answer to "what is a
document" on a platform whose answer is already "an HTML page". It is gone; see
`toto/cyprian/htmldoc.py`.

THIS MIGRATION DESTROYS DATA, AND THAT IS DELIBERATE. Read this before running
it on anything you cannot lose.

  1. A CTML file that is a WIKI PAGE — one a `kanban.DocumentationPage` points
     at — is CONVERTED IN PLACE: its bytes are rewritten from CTML XML to the
     HTML page they wrapped, and its row is retyped to 'html'. Nothing is lost
     and the page keeps its editable source. This is the population that must
     survive, because `DocumentationPage.body_html` renders fine without the
     file but the writer would open BLANK on the next edit and the first save
     would overwrite a page's prose with nothing.

  2. Every OTHER CTML file — a standalone document somebody wrote in Office —
     has its row DELETED and its blob UNLINKED. Permanently. There is no
     export step, no limbo and no legacy compatibility; that was the explicit
     instruction, and this comment is the only warning the operator gets.

WHAT IT CANNOT REACH, for the reasons 0023 already recorded and which have not
changed: rows in non-local buckets (a read there is an HTTP call to a peer),
mirrored stubs (`mirror._upsert_stub` re-applies the peer's type on every
refresh, and `mirror.py` now normalises inbound 'ctml' to 'html' instead), and
ENCRYPTED rows, whose bytes are a sealed frame this process cannot open. An
unreachable row is left ALONE rather than deleted: destroying a file we cannot
read to check what it is would be the worst possible reading of "delete the
CTML data".

ORDER: row first, blob second — `vault/purge.py`'s order, for its stated
reason. A failed blob delete leaves an invisible orphan, which is strictly
better than a live row pointing at deleted bytes.

REVERSE is a hard error. Nothing brings deleted bytes back, and pretending
otherwise with a no-op `RunPython.noop` would let `migrate 0023` report success
on a database that lost half its documents.
"""

import re

from django.db import migrations, models

#: Frozen copies. This migration must not import toto.cyprian: toto-base may
#: not import toto-works (a one-way package edge `check_package_graph.py`
#: enforces), and a host may install the vault with no writer on disk at all.
_BODY = re.compile(r"<content[^>]*>(.*?)</content\s*>", re.I | re.S)
_CDATA = re.compile(r"^\s*<!\[CDATA\[(.*?)\]\]>\s*$", re.S)
_TITLE = re.compile(r'\btitle="([^"]*)"', re.I)


def _text(value):
    return ((value or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _to_html(xml: str) -> str:
    """The HTML page a CTML document was wrapping.

    Deliberately forgiving: a document whose `<content>` cannot be found yields
    an empty body rather than raising, because a wiki page with an empty
    editable source is recoverable by hand and a failed migration mid-run is
    not.
    """
    match = _BODY.search(xml or "")
    body = match.group(1) if match else ""
    cdata = _CDATA.match(body)
    if cdata:
        body = cdata.group(1)
    title_match = _TITLE.search(xml or "")
    title = title_match.group(1) if title_match else ""
    return (
        '<!doctype html>\n<html>\n<head>\n<meta charset="utf-8">\n'
        f"<title>{_text(title)}</title>\n"
        "<style>\nbody { font-family: Georgia, 'Times New Roman', serif; "
        "margin: 3rem auto; max-width: 46rem; line-height: 1.5; }\n"
        "</style>\n</head>\n<body>\n"
        f"{body}\n</body>\n</html>\n"
    )


def _is_local(vault_file) -> bool:
    bucket = vault_file.bucket
    if bucket is None:
        return True
    return (getattr(bucket, "storage_backend", "") or "") in ("", "local")


def forward(apps, schema_editor):
    VaultFile = apps.get_model("vault", "VaultFile")

    rows = (VaultFile.objects
            .filter(file_type__in=("ctml", "document"))
            .exclude(origin="mirror")
            .select_related("bucket"))

    doomed_blobs = []
    for row in rows.iterator():
        if not _is_local(row) or row.is_encrypted:
            continue

        # A wiki page is one a DocumentationPage points at. Asked through the
        # reverse relation rather than by importing kanban, so this runs on a
        # host that never installed the boards.
        try:
            is_wiki = row.kanban_wiki_pages.exists()
        except Exception:            # noqa: BLE001 - no kanban on this host
            is_wiki = False

        if is_wiki:
            try:
                with row.file.storage.open(row.file.name, "rb") as fh:
                    xml = fh.read().decode("utf-8", "replace")
            except Exception:        # noqa: BLE001 - one bad file must not abort
                continue
            html = _to_html(xml).encode("utf-8")
            try:
                with row.file.storage.open(row.file.name, "wb") as fh:
                    fh.write(html)
            except Exception:        # noqa: BLE001
                continue
            VaultFile.objects.filter(pk=row.pk).update(
                file_type="html", file_size_bytes=len(html))
            continue

        if row.file:
            doomed_blobs.append((row.file.storage, row.file.name))
        VaultFile.objects.filter(pk=row.pk).delete()

    # Bytes after rows, never before.
    for storage, name in doomed_blobs:
        try:
            storage.delete(name)
        except Exception:            # noqa: BLE001 - an orphan beats a dangling row
            continue


def backward(apps, schema_editor):
    raise RuntimeError(
        "0024 cannot be reversed: it deleted standalone CTML documents and "
        "their bytes. Restore from a backup instead.")


class Migration(migrations.Migration):

    dependencies = [
        ("vault", "0023_ctml_file_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="vaultfile",
            name="file_type",
            field=models.CharField(choices=[('pdf', 'PDF'), ('image', 'Image'), ('html', 'HTML'), ('text', 'Text File'), ('json', 'JSON'), ('yaml', 'YAML'), ('xml', 'XML'), ('latex', 'LaTeX'), ('bib', 'Bibliography'), ('csv', 'CSV'), ('svg', 'SVG File'), ('audio', 'Audio'), ('video', 'Video'), ('python', 'Python'), ('neojson', 'NeoJSON'), ('sheet', 'Primula Sheet'), ('pxml', 'Presentation'), ('presentation', 'Presentation'), ('zip', 'Archive')], max_length=16),
        ),
        migrations.RunPython(forward, backward),
    ]
