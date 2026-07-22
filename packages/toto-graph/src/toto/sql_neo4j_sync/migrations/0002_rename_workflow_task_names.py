"""Rename the moved predefined-task names on existing WorkflowNode rows.

The SQL→Neo4j sync tasks were renamed ``ravioli_* → sql_neo4j_sync_*`` when they
moved to this app; existing workflows (e.g. created by the old ingress_ravioli)
still reference the old names, so rewrite them here.
"""

from django.db import migrations

RENAMES = {
    "ravioli_generate_plan": "sql_neo4j_sync_generate_plan",
    "ravioli_apply_plan": "sql_neo4j_sync_apply_plan",
    "ravioli_clear_db": "sql_neo4j_sync_clear_db",
    "ravioli_load_neojson": "sql_neo4j_sync_load_neojson",
}


def _rename(apps, mapping):
    try:
        WorkflowNode = apps.get_model("workflows", "WorkflowNode")
    except LookupError:
        return  # workflows app not installed in this build — nothing to do
    for old, new in mapping.items():
        WorkflowNode.objects.filter(task_name=old).update(task_name=new)


def forward(apps, schema_editor):
    _rename(apps, RENAMES)


def backward(apps, schema_editor):
    _rename(apps, {new: old for old, new in RENAMES.items()})


class Migration(migrations.Migration):

    dependencies = [
        ("sql_neo4j_sync", "0001_initial"),
        ("workflows", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]
