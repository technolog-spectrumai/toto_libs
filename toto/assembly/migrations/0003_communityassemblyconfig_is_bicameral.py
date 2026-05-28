from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assembly', '0002_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='communityassemblyconfig',
            name='is_bicameral',
            field=models.BooleanField(
                default=False,
                help_text="When True, proposals that pass the popular vote go to senate review before enactment. Senators are the community's senior members.",
            ),
        ),
    ]
