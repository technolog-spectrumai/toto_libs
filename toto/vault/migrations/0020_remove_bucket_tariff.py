from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("vault", "0019_storage_provider"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="bucket",
            name="tariff",
        ),
    ]
