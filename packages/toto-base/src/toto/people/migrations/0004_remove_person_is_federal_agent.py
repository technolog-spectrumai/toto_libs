from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0003_person_show_contact"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="person",
            name="is_federal_agent",
        ),
    ]
