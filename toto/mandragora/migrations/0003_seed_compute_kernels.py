from django.db import migrations


def seed_kernels(apps, schema_editor):
    ComputeKernel = apps.get_model("mandragora", "ComputeKernel")
    ComputeKernel.objects.get_or_create(
        name="Python 3",
        defaults={"timeout_ms": 30000, "env": {}, "dependencies": []},
    )


class Migration(migrations.Migration):

    dependencies = [("mandragora", "0002_backfill_notebook_slugs")]

    operations = [
        migrations.RunPython(seed_kernels, migrations.RunPython.noop),
    ]
