from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workflows", "0009_workflow_spine_redesign"),
    ]

    operations = [
        migrations.AlterField(
            model_name="workflownode",
            name="node_type",
            field=models.CharField(
                choices=[
                    ("lambda", "Lambda"),
                    ("split", "Split"),
                    ("join", "Join"),
                    ("report", "Report"),
                    ("predefined_task", "Predefined Task"),
                ],
                max_length=20,
            ),
        ),
    ]
