from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("tariffs", "0004_billing_unit_model"),
        ("vault", "0007_bucket_storage_quota"),
    ]

    operations = [
        migrations.AddField(
            model_name="bucket",
            name="tariff",
            field=models.ForeignKey(
                blank=True,
                help_text="Billing tariff for this bucket. Defaults to FILE-STORAGE when blank.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="buckets",
                to="tariffs.tariff",
            ),
        ),
    ]
