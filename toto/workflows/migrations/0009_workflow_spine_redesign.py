"""
Workflow spine redesign:
- Remove WorkflowConnector, WorkflowTriggerInput, HumanTask, WorkflowRunFile models
- Remove connector FK from WorkflowNode
- Add slug field to Workflow
- Remove PAUSED status from WorkflowRun
- Remove WAITING status from WorkflowNodeRun
"""

import re

from django.db import migrations, models
import django.utils.timezone


def _backfill_slugs(apps, schema_editor):
    Workflow = apps.get_model("workflows", "Workflow")
    seen = set(Workflow.objects.exclude(slug="").values_list("slug", flat=True))
    for wf in Workflow.objects.filter(slug=""):
        base = re.sub(r"[^a-z0-9]+", "-", wf.name.lower()).strip("-") or "workflow"
        slug = base
        n = 1
        while slug in seen:
            slug = f"{base}-{n}"
            n += 1
        wf.slug = slug
        wf.save(update_fields=["slug"])
        seen.add(slug)


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

        # 7. Back-fill slug from name for existing rows (Python — works on SQLite and PostgreSQL)
        migrations.RunPython(_backfill_slugs, migrations.RunPython.noop),

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
