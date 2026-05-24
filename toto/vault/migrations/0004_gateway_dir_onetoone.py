from django.db import migrations, models
import django.db.models.deletion


def delete_all_gateways(apps, schema_editor):
    """
    Wipe all gateways before restructuring the table.
    The ingress command re-seeds them with correct directory assignments.
    """
    apps.get_model("vault", "FileGateway").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("vault", "0003_gateway_directory"),
    ]

    operations = [
        # 1. Clear all legacy gateways so the unique constraint can be applied.
        migrations.RunPython(delete_all_gateways, migrations.RunPython.noop),

        # 2. bucket: OneToOneField → ForeignKey (drop unique constraint).
        migrations.AlterField(
            model_name="filegateway",
            name="bucket",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="gateways",
                to="vault.bucket",
            ),
        ),

        # 3. directory: ForeignKey(null=True) → OneToOneField(not null).
        migrations.AlterField(
            model_name="filegateway",
            name="directory",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="gateway",
                to="vault.vaultdirectory",
            ),
        ),
    ]
