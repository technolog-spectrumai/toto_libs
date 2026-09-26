"""A claim names the faucet payout it produced (2026-09-26)."""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("mana", "0001_initial"),
        ("assets", "0009_faucet_sources"),
    ]

    operations = [
        migrations.AddField(
            model_name="managrant", name="payout",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="mana_grants", to="assets.faucetpayout"),
        ),
    ]
