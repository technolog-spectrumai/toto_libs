from django.core.files.storage import default_storage
from django.db import migrations, models


def backfill_file_sizes(apps, schema_editor):
    VaultFile = apps.get_model("vault", "VaultFile")
    for vf in VaultFile.objects.filter(file_size_bytes=0).exclude(file=""):
        try:
            vf.file_size_bytes = default_storage.size(vf.file.name)
            vf.save(update_fields=["file_size_bytes"])
        except Exception:
            pass


class Migration(migrations.Migration):

    dependencies = [
        ("vault", "0004_gateway_dir_onetoone"),
    ]

    operations = [
        migrations.AddField(
            model_name="vaultfile",
            name="file_size_bytes",
            field=models.PositiveBigIntegerField(
                default=0,
                help_text="File size in bytes, captured at upload time.",
            ),
        ),
        migrations.RunPython(backfill_file_sizes, migrations.RunPython.noop),
    ]
