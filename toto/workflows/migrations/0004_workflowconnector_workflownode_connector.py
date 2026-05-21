import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workflows", "0003_workflownoderun_celery_task_id"),
    ]

    operations = [
        migrations.CreateModel(
            name="WorkflowConnector",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255, unique=True)),
                ("connector_type", models.CharField(
                    choices=[
                        ("file_read", "File read"),
                        ("file_write", "File write"),
                        ("api_request", "API request"),
                        ("people_read", "People read"),
                        ("socialhub_read", "Socialhub read"),
                        ("locations_read", "Locations read"),
                    ],
                    max_length=40,
                )),
                ("config", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
        ),
        migrations.AddField(
            model_name="workflownode",
            name="connector",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="workflow_nodes",
                to="workflows.workflowconnector",
            ),
        ),
        migrations.AlterField(
            model_name="workflownode",
            name="node_type",
            field=models.CharField(
                choices=[
                    ("lambda", "Lambda"),
                    ("human", "Human"),
                    ("split", "Split"),
                    ("join", "Join"),
                    ("connector", "Connector"),
                ],
                max_length=20,
            ),
        ),
    ]
