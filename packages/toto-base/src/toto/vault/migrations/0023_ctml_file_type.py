"""Written documents become their own file class, 'ctml'.

WHY. A Cyprian document was typed 'document' — a name that says nothing — and
stored in a file named `.xml`, an extension `toto.editor` already owns. So the
type could not be derived from the file: uploading a document by hand produced
`file_type='xml'`, which opens in ACE, and only cyprian's own `_adopt()` ever
repaired the row — and only if the OWNER happened to open it. What a file WAS
depended on which editor had touched it last. `.ctml` puts the answer in the
name and `_EXT_MAP` turns it into a type at every ingest door.

The format is CTML; the editor stays Cyprian. That distinction is the whole
point of the release this migration belongs to.

WHAT THIS DOES.
  1. AlterField, so the model and the schema agree — `makemigrations --check`
     is a gate step.
  2. Retypes `document` -> `ctml` by NAME. No bytes are read, so encrypted rows
     migrate correctly too.
  3. Retypes `xml` rows whose CONTENT is a document. This is the one
     non-repeating chance to catch the population `_adopt()` never reached:
     documents uploaded by hand and never opened by their owner.

WHAT IT DELIBERATELY DOES NOT DO: rename titles. 0021 renamed `.xml` -> `.pxml`
here, and this one does not, because the decision for CTML was to keep existing
filenames. The consequence is real and is the reason `_adopt()` SURVIVES rather
than being deleted the way memo's sniff was: a `.ctml`-less document that is
downloaded and re-uploaded comes back as `xml`, and `_adopt()` is what repairs
it on the next open. New documents are named `.ctml` and need no such help.

WHAT IT DELIBERATELY DOES NOT REACH, and why each one is safe to skip — the
same three populations 0021 could not reach, for the same reasons:
  * MIRRORED rows (`origin='mirror'`). `mirror._upsert_stub` re-applies the
    peer's file_type on every refresh, so retyping a stub is churn that undoes
    itself. Inbound normalisation in `mirror.py` handles those instead.
  * Rows in non-local buckets (s3, remote_toto). The historical model's
    FileField is local `private_storage`; for `remote_toto` a read is an HTTP
    call to a peer. A blind read there is wrong, not merely slow.
  * ENCRYPTED rows typed 'xml'. The bytes are a sealed frame, so a document
    cannot be told from a deck or a notebook. Permanently unidentifiable; the
    remedy is manual and it exists — the Rename dialog's type dropdown, which
    offers 'CTML Document'.
  * Any row whose bytes cannot be read. Each read is guarded individually so
    one missing file cannot abort a migrate that has already committed the
    AlterField.

THE SNIFF IS INLINED ON PURPOSE. It is a frozen copy of
`toto.cyprian.ctml.sniff_is_document` as of this migration, and it must stay a
copy: toto-base may not import toto-works (the package graph is a one-way edge
and `check_package_graph.py` fails on the cycle), and a host may install
toto.vault with no toto-works on disk at all. Same reasoning as 0021.

REVERSE is deliberate and lossy: everything goes back to 'document'. Forward
merges two populations ('document' rows and sniffed 'xml' rows) and the
distinction is not recoverable.
"""
import re

from django.db import migrations, models

# Frozen copy — see the docstring. Do not import this from toto.cyprian.
_SNIFF_RE = re.compile(rb"<\s*document\s*[/>]|<\s*document\s", re.IGNORECASE)
_SNIFF_SKIP = re.compile(rb"<\?xml[^>]*\?>|<!--.*?-->|<!DOCTYPE[^>]*>", re.DOTALL)


#: Matches the ROOT tag only, so a memo <presentation> or a mandragora
#: <notebook> — both also stored as file_type='xml' — are not swallowed.
def _looks_like_a_document(head: bytes) -> bool:
    head = _SNIFF_SKIP.sub(b"", head[:2048]).lstrip()
    return bool(_SNIFF_RE.match(head))


def _is_local(vault_file) -> bool:
    """Only rows whose bytes this process can actually open."""
    bucket = vault_file.bucket
    if bucket is None:
        return True
    return (getattr(bucket, "storage_backend", "") or "") in ("", "local")


def forward(apps, schema_editor):
    VaultFile = apps.get_model("vault", "VaultFile")

    # (1) By name. Cheap, exact, and correct for encrypted rows too.
    VaultFile.objects.filter(file_type="document").exclude(
        origin="mirror").update(file_type="ctml")

    # (2) By content, for every document that was filed as generic XML —
    # the ones `_adopt()` never saw because nobody opened them.
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
        if _looks_like_a_document(head):
            VaultFile.objects.filter(pk=row.pk).update(file_type="ctml")


def backward(apps, schema_editor):
    VaultFile = apps.get_model("vault", "VaultFile")
    VaultFile.objects.filter(file_type="ctml").update(file_type="document")


class Migration(migrations.Migration):

    dependencies = [
        ("vault", "0022_sealed_credentials"),
    ]

    operations = [
        migrations.AlterField(
            model_name="vaultfile",
            name="file_type",
            field=models.CharField(choices=[('pdf', 'PDF'), ('image', 'Image'), ('html', 'HTML'), ('text', 'Text File'), ('json', 'JSON'), ('yaml', 'YAML'), ('xml', 'XML'), ('latex', 'LaTeX'), ('bib', 'Bibliography'), ('csv', 'CSV'), ('svg', 'SVG File'), ('audio', 'Audio'), ('video', 'Video'), ('python', 'Python'), ('neojson', 'NeoJSON'), ('sheet', 'Primula Sheet'), ('pxml', 'Presentation'), ('presentation', 'Presentation'), ('ctml', 'CTML Document'), ('document', 'Document'), ('zip', 'Archive')], max_length=16),
        ),
        migrations.RunPython(forward, backward),
    ]
