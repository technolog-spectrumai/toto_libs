"""Back-fill: mark existing SQL-synced categories as internal (one-time).

New synced categories are created ``internal=True``; this back-fills the ones that
already exist so the Ingestor's LLM stops offering infrastructure types
(kanban-doc-section, …). Synced categories are identified by their seeded
description ("Synced from …"). Runs once — later user toggles are preserved.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    BentoCategory = apps.get_model("bento", "BentoCategory")
    BentoCategory.objects.filter(
        description__startswith="Synced from ", internal=False
    ).update(internal=True)


def backwards(apps, schema_editor):
    # One-way; un-mark individual categories via the Bento UI if needed.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("bento", "0008_concept_note_name_schema"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
