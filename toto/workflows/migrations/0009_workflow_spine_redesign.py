"""
Workflow spine redesign:
- Remove WorkflowConnector, WorkflowTriggerInput, HumanTask, WorkflowRunFile models
- Remove connector FK from WorkflowNode
- Add slug field to Workflow
- Remove PAUSED status from WorkflowRun
- Remove WAITING status from WorkflowNodeRun
"""

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("workflows", "0008_alter_workflownode_node_type_workflowtriggerinput_and_more"),
    ]

    operations = [
        # 1. Drop HumanTask (depends on WorkflowNodeRun)
        migrations.DeleteModel(name="HumanTask"),

        # 2. Drop WorkflowRunFile (depends on WorkflowRun and WorkflowTriggerInput)
        migrations.DeleteModel(name="WorkflowRunFile"),

        # 3. Drop WorkflowTriggerInput (depends on WorkflowNode)
        migrations.DeleteModel(name="WorkflowTriggerInput"),

        # 4. Remove connector FK from WorkflowNode
        migrations.RemoveField(model_name="workflownode", name="connector"),

        # 5. Drop WorkflowConnector (no more dependents)
        migrations.DeleteModel(name="WorkflowConnector"),

        # 6. Add slug to Workflow
        migrations.AddField(
            model_name="workflow",
            name="slug",
            field=models.SlugField(max_length=280, unique=True, blank=True, default=""),
            preserve_default=False,
        ),

        # 7. Back-fill slug from name for existing rows using a data migration step
        migrations.RunSQL(
            sql="""
                UPDATE workflows_workflow SET slug = LOWER(REGEXP_REPLACE(name, '[^a-zA-Z0-9]+', '-', 'g'))
                WHERE slug = '' OR slug IS NULL;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),

        # 8. Update WorkflowRun STATUS_CHOICES — remove PAUSED (DB column unchanged, just choices)
        migrations.AlterField(
            model_name="workflowrun",
            name="status",
            field=models.CharField(
                max_length=20,
                choices=[
                    ("pending", "Pending"),
                    ("running", "Running"),
                    ("completed", "Completed"),
                    ("failed", "Failed"),
                ],
                default="pending",
            ),
        ),

        # 9. Update WorkflowNodeRun STATUS_CHOICES — remove WAITING
        migrations.AlterField(
            model_name="workflownoderun",
            name="status",
            field=models.CharField(
                max_length=20,
                choices=[
                    ("pending", "Pending"),
                    ("running", "Running"),
                    ("completed", "Completed"),
                    ("failed", "Failed"),
                    ("skipped", "Skipped"),
                ],
                default="pending",
            ),
        ),

        # 10. Update WorkflowNode NODE_TYPES — remove trigger/human/connector
        migrations.AlterField(
            model_name="workflownode",
            name="node_type",
            field=models.CharField(
                max_length=20,
                choices=[
                    ("lambda", "Lambda"),
                    ("split", "Split"),
                    ("join", "Join"),
                    ("report", "Report"),
                ],
            ),
        ),
    ]
