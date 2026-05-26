from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0007_remove_asset_mint_transaction_type'),
        ('tariffs', '0001_initial'),
        ('vault', '0011_storageaccount_authorization'),
    ]

    operations = [
        migrations.CreateModel(
            name='BucketBilling',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('storage_quota_mb', models.PositiveIntegerField(blank=True, help_text='Per-user storage quota in MB. Overrides the bucket default when set.', null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('bucket', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='billing', to='vault.bucket')),
                ('tariff', models.ForeignKey(blank=True, help_text='Billing tariff override. Falls back to bucket tariff when blank.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='bucket_billings', to='tariffs.tariff')),
            ],
            options={
                'verbose_name': 'Bucket Billing',
                'verbose_name_plural': 'Bucket Billings',
            },
        ),
        migrations.CreateModel(
            name='StorageTokenPrice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('price_per_token', models.DecimalField(decimal_places=10, help_text='Cost of 1 STORAGE_TOKEN in this currency (e.g. 0.01 TUSD per token).', max_digits=30)),
                ('bucket_billing', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='token_prices', to='vault.bucketbilling')),
                ('currency', models.ForeignKey(help_text='Asset used to pay for storage tokens.', on_delete=django.db.models.deletion.CASCADE, related_name='storage_token_prices', to='assets.asset')),
                ('revenue_account', models.ForeignKey(help_text='Ledger account that receives payments in this currency.', on_delete=django.db.models.deletion.PROTECT, related_name='storage_token_revenues', to='assets.ledgeraccount')),
            ],
            options={
                'verbose_name': 'Storage Token Price',
                'verbose_name_plural': 'Storage Token Prices',
                'unique_together': {('bucket_billing', 'currency')},
            },
        ),
        migrations.AddField(
            model_name='bucketbilling',
            name='allowed_currencies',
            field=models.ManyToManyField(blank=True, help_text='Assets accepted as payment for storage tokens.', related_name='bucket_billings', through='vault.StorageTokenPrice', to='assets.asset'),
        ),
    ]
