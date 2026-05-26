from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('vault', '0012_bucketbilling'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Remove billing models (StorageTokenPrice first — it is the M2M through table)
        migrations.DeleteModel(name='StorageTokenPrice'),
        migrations.DeleteModel(name='BucketBilling'),
        migrations.DeleteModel(name='StorageAccount'),

        # Add standalone invoice model
        migrations.CreateModel(
            name='VaultInvoice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=18)),
                ('currency_label', models.CharField(
                    default='USD',
                    max_length=20,
                    help_text='Currency code or label, e.g. USD, PLN, EUR.',
                )),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'Pending'),
                        ('paid', 'Paid'),
                        ('overdue', 'Overdue'),
                        ('cancelled', 'Cancelled'),
                    ],
                    default='pending',
                    max_length=20,
                )),
                ('due_date', models.DateField(blank=True, null=True)),
                ('paid_at', models.DateTimeField(blank=True, null=True)),
                ('notes', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('issued_to', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='vault_invoices',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('issued_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='issued_vault_invoices',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Vault Invoice',
                'verbose_name_plural': 'Vault Invoices',
                'ordering': ['-created_at'],
            },
        ),
    ]
