from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0004_inventorysite_is_virtual"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="realworldobject",
            name="estimated_value_currency",
        ),
    ]
