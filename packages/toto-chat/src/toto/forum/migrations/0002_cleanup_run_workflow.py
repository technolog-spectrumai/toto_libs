from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("forum", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="forumcleanuprun",
            name="workflow_run_id",
            field=models.PositiveBigIntegerField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="forumcleanuprun",
            name="task_id",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
