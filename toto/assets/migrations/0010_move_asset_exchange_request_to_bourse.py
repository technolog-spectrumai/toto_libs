from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0009_alter_assetexchangerequest_counterparty"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(
                    name="AssetExchangeRequest",
                ),
            ],
        ),
    ]
