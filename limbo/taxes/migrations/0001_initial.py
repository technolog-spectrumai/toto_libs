from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("assets", "0003_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="TransactionFeePolicy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=160)),
                ("description", models.TextField(blank=True)),
                ("fee_rate_bps", models.PositiveIntegerField(default=0, help_text="Fee in basis points (1 bps = 0.01%). Applied proportionally to transaction amount.")),
                ("fixed_amount_base_units", models.BigIntegerField(default=0, help_text="Fixed fee in asset base units applied per transaction (on top of rate).")),
                ("scope", models.CharField(choices=[("all", "All transactions"), ("external", "External transfers only"), ("internal", "Internal transfers only")], default="all", max_length=20)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("asset", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="fee_policies", to="assets.asset")),
                ("receiving_account", models.ForeignKey(help_text="Account that receives the collected fee.", on_delete=django.db.models.deletion.PROTECT, related_name="tax_receiving_policies", to="assets.ledgeraccount")),
            ],
            options={"verbose_name": "Transaction Fee Policy", "verbose_name_plural": "Transaction Fee Policies", "ordering": ["asset__unit_name", "name"]},
        ),
        migrations.CreateModel(
            name="TransactionFee",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount_base_units", models.BigIntegerField()),
                ("transaction_amount_base_units", models.BigIntegerField(help_text="The gross transaction amount the fee was calculated from.")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("policy", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="charges", to="taxes.transactionfeepolicy")),
                ("transaction", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="tax_charges", to="assets.ledgertransaction")),
                ("asset", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="tax_charges", to="assets.asset")),
            ],
            options={"verbose_name": "Transaction Fee", "verbose_name_plural": "Transaction Fees", "ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="transactionfee",
            index=models.Index(fields=["asset", "created_at"], name="taxes_fee_asset_created_idx"),
        ),
        migrations.AddIndex(
            model_name="transactionfee",
            index=models.Index(fields=["policy", "created_at"], name="taxes_fee_policy_created_idx"),
        ),
    ]
