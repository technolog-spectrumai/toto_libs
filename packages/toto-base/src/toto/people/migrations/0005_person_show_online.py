from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0004_remove_person_is_federal_agent'),
    ]

    operations = [
        migrations.AddField(
            model_name='person',
            name='show_online',
            field=models.BooleanField(default=True, help_text="Whether members of this person's communities are told when they sign in and out. On by default."),
        ),
    ]
