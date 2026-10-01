from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("socialhub", "0004_privacynotice_seeded"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="community",
            name="is_federal_tribe",
        ),
    ]
