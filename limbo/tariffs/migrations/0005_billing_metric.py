from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tariffs', '0004_billing_unit_model'),
    ]

    operations = [
        # Wipe dependent rows — all tariff data is re-seeded by ingress_tariffs.
        migrations.RunSQL(
            "DELETE FROM tariffs_usagecharge; "
            "DELETE FROM tariffs_usagerecord; "
            "DELETE FROM tariffs_tariffitem;",
            migrations.RunSQL.noop,
        ),

        # Create BillingMetric model.
        migrations.CreateModel(
            name='BillingMetric',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=100, unique=True)),
                ('label', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True)),
                ('default_unit', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='metrics',
                    to='tariffs.billingunit',
                )),
                ('dimension', models.CharField(blank=True, max_length=50)),
                ('app_label', models.CharField(blank=True, max_length=100)),
                ('active', models.BooleanField(default=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
            ],
            options={
                'ordering': ['dimension', 'code'],
            },
        ),

        # Drop the old unique_together constraint (tariff, code) before we touch fields.
        migrations.AlterUniqueTogether(
            name='tariffitem',
            unique_together=set(),
        ),

        # Remove the explicit index on TariffItem.code.
        migrations.RemoveIndex(
            model_name='tariffitem',
            name='tariffs_tar_code_73de00_idx',
        ),

        # Drop the old code CharField.
        migrations.RemoveField(
            model_name='tariffitem',
            name='code',
        ),

        # Add metric FK — non-null immediately since table is empty after the DELETE above.
        migrations.AddField(
            model_name='tariffitem',
            name='metric',
            field=models.ForeignKey(
                help_text='Billing metric this item charges for, e.g. storage.request, ai.input_tokens',
                on_delete=django.db.models.deletion.PROTECT,
                related_name='tariff_items',
                to='tariffs.billingmetric',
            ),
        ),

        # Apply new unique_together constraint.
        migrations.AlterUniqueTogether(
            name='tariffitem',
            unique_together={('tariff', 'metric')},
        ),

        # Update model options (ordering now uses metric__code).
        migrations.AlterModelOptions(
            name='tariffitem',
            options={'ordering': ['metric__code']},
        ),
    ]
