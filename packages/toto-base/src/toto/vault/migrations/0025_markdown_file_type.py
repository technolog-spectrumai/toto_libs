"""Markdown becomes its own file class; `.md` stops being 'text'.

WHY. `_EXT_MAP` sent `.md` to 'text', so a README opened in a plain-text editor
with no highlighting, and nothing downstream could tell prose-with-structure
from a log file. The Office work needs the distinction — a markdown file is
what gets rendered to PDF — and a type is the only place to put it.

WHAT THIS DOES.
  1. AlterField, so the model and the schema agree (`makemigrations --check`
     is a gate step on both hosts).
  2. Retypes existing 'text' rows whose TITLE ends `.md` or `.markdown`.

BY NAME ONLY, and no content sniffing at all — unlike 0021, which had to sniff
because a deck and a document were both `.xml` and indistinguishable by name.
Markdown has no such problem: it IS text, and text with a `.md` name is the
whole definition. Reading bytes could only produce false positives, since any
plain-text file is also valid markdown.

Because it reads no bytes, this reaches ENCRYPTED and REMOTE rows correctly
too — the two populations 0021 had to skip and could never recover.

DELIBERATELY SKIPPED: mirrored rows (`origin='mirror'`). `mirror._upsert_stub`
re-applies the peer's file_type on every refresh, so retyping a stub is churn
that undoes itself. Same reasoning as 0021.

The title is NOT rewritten. 0021 renamed `.xml` to `.pxml` because the
extension was the thing it was making authoritative; here the extension is
already right and already what `_EXT_MAP` reads.

REVERSE puts every 'markdown' row back to 'text', which is exactly where they
came from and where a pre-0025 host would have put them.
"""

from django.db import migrations, models

_SUFFIXES = (".md", ".markdown")


def forward(apps, schema_editor):
    VaultFile = apps.get_model("vault", "VaultFile")
    rows = VaultFile.objects.filter(file_type="text").exclude(origin="mirror")
    for row in rows.iterator():
        if (row.title or "").lower().endswith(_SUFFIXES):
            VaultFile.objects.filter(pk=row.pk).update(file_type="markdown")


def backward(apps, schema_editor):
    VaultFile = apps.get_model("vault", "VaultFile")
    VaultFile.objects.filter(file_type="markdown").update(file_type="text")


class Migration(migrations.Migration):

    dependencies = [
        ("vault", "0024_retire_ctml"),
    ]

    operations = [
        migrations.AlterField(
            model_name="vaultfile",
            name="file_type",
            field=models.CharField(choices=[('pdf', 'PDF'), ('image', 'Image'), ('html', 'HTML'), ('text', 'Text File'), ('markdown', 'Markdown'), ('json', 'JSON'), ('yaml', 'YAML'), ('xml', 'XML'), ('latex', 'LaTeX'), ('bib', 'Bibliography'), ('csv', 'CSV'), ('svg', 'SVG File'), ('audio', 'Audio'), ('video', 'Video'), ('python', 'Python'), ('neojson', 'NeoJSON'), ('sheet', 'Primula Sheet'), ('pxml', 'Presentation'), ('presentation', 'Presentation'), ('zip', 'Archive')], max_length=16),
        ),
        migrations.RunPython(forward, backward),
    ]
