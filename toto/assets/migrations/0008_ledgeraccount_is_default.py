from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0007_remove_asset_mint_transaction_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="ledgeraccount",
            name="user_priority",
            field=models.IntegerField(
                default=0,
                help_text="Billing priority for this user's accounts. Higher = checked first.",
            ),
        ),
    ]
