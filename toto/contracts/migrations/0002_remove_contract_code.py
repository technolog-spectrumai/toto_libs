from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("contracts", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="contract",
            name="code",
        ),
    ]
