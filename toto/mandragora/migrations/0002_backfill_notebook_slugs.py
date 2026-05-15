from django.db import migrations
from django.utils.text import slugify


def backfill_slugs(apps, schema_editor):
    Notebook = apps.get_model("mandragora", "Notebook")
    for nb in Notebook.objects.filter(slug=""):
        base = slugify(nb.title) or f"notebook-{nb.pk}"
        slug = base
        n = 1
        while Notebook.objects.filter(slug=slug).exclude(pk=nb.pk).exists():
            slug = f"{base}-{n}"
            n += 1
        nb.slug = slug
        nb.save(update_fields=["slug"])


class Migration(migrations.Migration):

    dependencies = [
        ("mandragora", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(backfill_slugs, migrations.RunPython.noop),
    ]
