from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0006_asset_reserve_account'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ledgertransaction',
            name='transaction_type',
            field=models.CharField(
                choices=[
                    ('asset_create', 'Asset Create'),
                    ('asset_transfer', 'Asset Transfer'),
                    ('reversal', 'Reversal'),
                    ('adjustment', 'Adjustment'),
                ],
                max_length=30,
            ),
        ),
    ]
