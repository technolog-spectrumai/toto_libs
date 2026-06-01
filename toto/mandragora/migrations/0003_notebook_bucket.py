import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mandragora", "0002_computekernel_startup_timeout_ms"),
        ("vault", "0021_alter_bucket_storage_config"),
    ]

    operations = [
        migrations.AddField(
            model_name="notebook",
            name="bucket",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Vault bucket attached to this notebook. "
                    "Files are injected as vault_files dict so you can open(vault_files['name'])."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="notebooks",
                to="vault.bucket",
            ),
        ),
    ]
