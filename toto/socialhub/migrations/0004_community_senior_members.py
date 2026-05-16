# Generated manually for community news permissions.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0001_initial"),
        ("socialhub", "0003_alter_community_email_service_delete_emailservice"),
    ]

    operations = [
        migrations.AddField(
            model_name="community",
            name="senior_members",
            field=models.ManyToManyField(
                blank=True,
                help_text="Members allowed to manage community announcements and news.",
                related_name="senior_communities",
                to="people.person",
            ),
        ),
    ]
