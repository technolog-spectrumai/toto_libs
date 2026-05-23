from django.db import migrations


def backfill_task_name(apps, schema_editor):
    WorkflowNode = apps.get_model("workflows", "WorkflowNode")
    nodes = WorkflowNode.objects.filter(
        node_type="predefined_task",
        task_name="",
    ).exclude(config={})
    for node in nodes:
        task_name = (node.config or {}).get("task_name", "")
        if task_name:
            node.task_name = task_name
            node.save(update_fields=["task_name"])


class Migration(migrations.Migration):

    dependencies = [
        ("workflows", "0011_workflownode_task_name"),
    ]

    operations = [
        migrations.RunPython(backfill_task_name, migrations.RunPython.noop),
    ]
