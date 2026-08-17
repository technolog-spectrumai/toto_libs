"""Slide decks become their own file class, 'pxml', named `*.pxml`.

WHY. A deck was typed 'presentation' but stored in a file named `.xml`, so
nothing could tell one from a cyprian document or a notebook without reading the
bytes. toto.memo therefore content-sniffed up to 300 files on every gallery
visit and retyped rows behind the user's back. `.pxml` puts the answer in the
name, `_EXT_MAP` turns it into a type at every ingest door, and the sniffing is
deleted in the same release as this migration.

WHAT THIS DOES.
  1. AlterField, so the model and the schema agree — `makemigrations --check`
     is a gate step on both zenobia and placidia.
  2. Retypes `presentation` -> `pxml` by NAME. No bytes are read, so encrypted
     rows migrate correctly too.
  3. Retypes `xml` rows whose CONTENT is a deck. This is the one non-repeating
     chance to catch them: once memo's sniff is gone, nothing retypes anything
     ever again.
  4. Renames the `.xml` title suffix to `.pxml` on rows it retypes. DB only —
     the stored file keeps its name. `api_views.FileDownloadApiView` serves
     `title` as the download filename, so this is what stops a
     download-then-re-upload round trip from typing the deck back to 'xml'.
     `key` is untouched: `VaultFile.save` derives it only when blank, so there
     is no collision to cause here.

WHAT IT DELIBERATELY DOES NOT REACH, and why each one is safe to skip:
  * MIRRORED rows (`origin='mirror'`). `mirror._upsert_stub` re-applies the
    peer's file_type on every refresh, so retyping a stub is churn that undoes
    itself. Inbound normalisation in `mirror.py` handles those instead.
  * Rows in non-local buckets (s3, remote_toto). The historical model's
    FileField is local `private_storage`; the per-bucket driver seam is app
    code a migration must not use, and for `remote_toto` reading means an HTTP
    call to a peer. A blind read there is wrong, not merely slow.
  * ENCRYPTED rows typed 'xml'. The bytes are a sealed frame, so a deck cannot
    be told from anything else. These are unidentifiable, permanently. The
    remedy is manual and it exists: the Rename dialog's type dropdown, which
    offers 'pxml'.
  * Any row whose bytes cannot be read. Each read is guarded individually so
    that one missing file cannot abort a migrate that has already committed
    the AlterField.

THE SNIFF IS INLINED ON PURPOSE. It is a frozen copy of
`toto.memo.presentation_format.sniff_is_presentation` as of this migration, and
it must stay a copy: toto-base may not import toto-works (the package graph is
a one-way edge and `check_package_graph.py` fails on the cycle), placidia
installs toto.vault with no toto-works on disk at all, and the original is
DELETED in this same release — an import would break on replay. Same reasoning
as the palimpsest 0002 sanitiser migration.

REVERSE is deliberate and lossy: everything goes back to 'presentation'. Forward
merges two populations ('presentation' rows and sniffed 'xml' rows) and the
distinction is not recoverable. 'presentation' is the correct destination
because it is exactly what pre-0021 memo would have converged every deck to
anyway — its filters accept `["xml", "presentation"]`. The title suffix is
reverted with it.
"""
import os
import re

from django.db import migrations, models

# Frozen copy — see the docstring. Do not import this from toto.memo.
_SNIFF_RE = re.compile(rb"<\s*presentation\s*[/>]|<\s*presentation\s", re.IGNORECASE)
_SNIFF_SKIP = re.compile(rb"<\?xml[^>]*\?>|<!--.*?-->|<!DOCTYPE[^>]*>", re.DOTALL)

#: Matches the root tag only, so a cyprian <document> or a mandragora <notebook>
#: — both also stored as file_type='xml' — are not swallowed.
def _looks_like_a_deck(head: bytes) -> bool:
    head = _SNIFF_SKIP.sub(b"", head[:2048]).lstrip()
    return bool(_SNIFF_RE.match(head))


def _is_local(vault_file) -> bool:
    """Only rows whose bytes this process can actually open."""
    bucket = vault_file.bucket
    if bucket is None:
        return True
    return (getattr(bucket, "storage_backend", "") or "") in ("", "local")


def _retitle(title: str, frm: str, to: str) -> str:
    stem, ext = os.path.splitext(title or "")
    return f"{stem}{to}" if ext.lower() == frm else (title or "")


def forward(apps, schema_editor):
    VaultFile = apps.get_model("vault", "VaultFile")

    # (1) By name. Cheap, exact, and correct for encrypted rows too.
    for row in VaultFile.objects.filter(file_type="presentation").exclude(origin="mirror"):
        VaultFile.objects.filter(pk=row.pk).update(
            file_type="pxml", title=_retitle(row.title, ".xml", ".pxml"))

    # (2) By content, for the era when decks were typed 'xml'.
    candidates = (
        VaultFile.objects
        .filter(file_type="xml", is_encrypted=False)
        .exclude(origin="mirror")
        .select_related("bucket")
    )
    for row in candidates.iterator():
        if not _is_local(row):
            continue
        try:
            with row.file.storage.open(row.file.name, "rb") as fh:
                head = fh.read(2048)
        except Exception:          # noqa: BLE001 - one bad file must not abort the migrate
            continue
        if _looks_like_a_deck(head):
            VaultFile.objects.filter(pk=row.pk).update(
                file_type="pxml", title=_retitle(row.title, ".xml", ".pxml"))


def backward(apps, schema_editor):
    VaultFile = apps.get_model("vault", "VaultFile")
    for row in VaultFile.objects.filter(file_type="pxml"):
        VaultFile.objects.filter(pk=row.pk).update(
            file_type="presentation", title=_retitle(row.title, ".pxml", ".xml"))


class Migration(migrations.Migration):

    dependencies = [
        ("vault", "0020_transferrun"),
    ]

    operations = [
        migrations.AlterField(
            model_name="vaultfile",
            name="file_type",
            field=models.CharField(choices=[('pdf', 'PDF'), ('image', 'Image'), ('html', 'HTML'), ('text', 'Text File'), ('json', 'JSON'), ('yaml', 'YAML'), ('xml', 'XML'), ('latex', 'LaTeX'), ('bib', 'Bibliography'), ('csv', 'CSV'), ('svg', 'SVG File'), ('audio', 'Audio'), ('video', 'Video'), ('python', 'Python'), ('neojson', 'NeoJSON'), ('sheet', 'Primula Sheet'), ('pxml', 'Presentation'), ('presentation', 'Presentation'), ('document', 'Document'), ('zip', 'Archive')], max_length=16),
        ),
        migrations.RunPython(forward, backward),
    ]
