import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("vault", "0014_vaultinvoice_bucket_obligation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BillingCycle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("frequency", models.CharField(
                    choices=[("daily", "Daily"), ("weekly", "Weekly"), ("monthly", "Monthly"), ("yearly", "Yearly")],
                    default="monthly",
                    max_length=20,
                )),
                ("description", models.TextField(blank=True)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Billing Cycle",
                "verbose_name_plural": "Billing Cycles",
                "ordering": ["frequency", "name"],
            },
        ),
        migrations.CreateModel(
            name="Invoice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=18)),
                ("currency_label", models.CharField(
                    default="USD",
                    help_text="Currency code or token unit name, e.g. USD, PLN, STORAGE_TOKEN.",
                    max_length=20,
                )),
                ("status", models.CharField(
                    choices=[("pending", "Pending"), ("paid", "Paid"), ("overdue", "Overdue"), ("cancelled", "Cancelled")],
                    default="pending",
                    max_length=20,
                )),
                ("due_date", models.DateField(blank=True, null=True)),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                ("notes", models.TextField(blank=True)),
                ("obligation_reference", models.CharField(
                    blank=True,
                    help_text="Reference of the assets Obligation created when the invoice was accepted.",
                    max_length=255,
                )),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("billing_cycle", models.ForeignKey(
                    blank=True,
                    help_text="Billing cycle that triggered this invoice (if any).",
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="invoices",
                    to="invoice.billingcycle",
                )),
                ("bucket", models.ForeignKey(
                    blank=True,
                    help_text="Vault bucket this invoice was generated for (if any).",
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="invoice_app_invoices",
                    to="vault.bucket",
                )),
                ("issued_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="issued_invoices",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("issued_to", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="invoices",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "verbose_name": "Invoice",
                "verbose_name_plural": "Invoices",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="PaymentSettlement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("settled_at", models.DateTimeField()),
                ("amount", models.DecimalField(decimal_places=2, max_digits=18)),
                ("currency_label", models.CharField(max_length=20)),
                ("method", models.CharField(
                    blank=True,
                    help_text="e.g. bank_transfer, ledger, cash",
                    max_length=100,
                )),
                ("reference", models.CharField(blank=True, max_length=255)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("invoice", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="settlements",
                    to="invoice.invoice",
                )),
            ],
            options={
                "verbose_name": "Payment Settlement",
                "verbose_name_plural": "Payment Settlements",
                "ordering": ["-settled_at"],
            },
        ),
    ]
