from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0010_move_asset_exchange_request_to_bourse"),
        ("detections", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="bountypayment",
            name="receiver_ledger_account",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="bounty_payments_received",
                to="assets.ledgeraccount",
            ),
        ),
    ]
