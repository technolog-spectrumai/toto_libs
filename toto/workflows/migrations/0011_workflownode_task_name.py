from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workflows", "0010_workflownode_predefined_task"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflownode",
            name="task_name",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
