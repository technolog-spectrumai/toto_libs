import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mandragora", "0002_kerneldependency_auto_close"),
    ]

    operations = [
        migrations.CreateModel(
            name="Workflow",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
        ),
        migrations.CreateModel(
            name="WorkflowNode",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("node_type", models.CharField(
                    choices=[("lambda", "Lambda"), ("human", "Human"), ("split", "Split"), ("join", "Join")],
                    max_length=20,
                )),
                ("label", models.CharField(blank=True, max_length=255)),
                ("config", models.JSONField(blank=True, default=dict)),
                ("position_x", models.FloatField(default=0.0)),
                ("position_y", models.FloatField(default=0.0)),
                ("workflow", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="nodes",
                    to="mandragora.workflow",
                )),
                ("lambda_function", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="workflow_nodes",
                    to="mandragora.lambdafunction",
                )),
            ],
        ),
        migrations.CreateModel(
            name="WorkflowEdge",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("branch_key", models.CharField(blank=True, max_length=255)),
                ("is_default", models.BooleanField(default=False)),
                ("workflow", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="edges",
                    to="mandragora.workflow",
                )),
                ("source", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="outgoing_edges",
                    to="mandragora.workflownode",
                )),
                ("target", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="incoming_edges",
                    to="mandragora.workflownode",
                )),
            ],
        ),
        migrations.AddConstraint(
            model_name="workflowedge",
            constraint=models.UniqueConstraint(fields=["source", "target"], name="unique_workflow_edge"),
        ),
        migrations.CreateModel(
            name="WorkflowRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(
                    choices=[
                        ("pending", "Pending"), ("running", "Running"), ("paused", "Paused"),
                        ("completed", "Completed"), ("failed", "Failed"),
                    ],
                    default="pending",
                    max_length=20,
                )),
                ("input_data", models.JSONField(blank=True, default=dict)),
                ("output_data", models.JSONField(blank=True, null=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("workflow", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="runs",
                    to="mandragora.workflow",
                )),
            ],
        ),
        migrations.CreateModel(
            name="WorkflowNodeRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(
                    choices=[
                        ("pending", "Pending"), ("running", "Running"), ("waiting", "Waiting for human"),
                        ("completed", "Completed"), ("failed", "Failed"), ("skipped", "Skipped"),
                    ],
                    default="pending",
                    max_length=20,
                )),
                ("input_data", models.JSONField(blank=True, null=True)),
                ("output_data", models.JSONField(blank=True, null=True)),
                ("error", models.TextField(blank=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("workflow_run", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="node_runs",
                    to="mandragora.workflowrun",
                )),
                ("node", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="node_runs",
                    to="mandragora.workflownode",
                )),
            ],
        ),
        migrations.AddConstraint(
            model_name="workflownoderun",
            constraint=models.UniqueConstraint(fields=["workflow_run", "node"], name="unique_workflow_node_run"),
        ),
        migrations.CreateModel(
            name="WorkflowEdgeRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("activated", models.BooleanField(default=False)),
                ("activated_at", models.DateTimeField(blank=True, null=True)),
                ("workflow_run", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="edge_runs",
                    to="mandragora.workflowrun",
                )),
                ("edge", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="edge_runs",
                    to="mandragora.workflowedge",
                )),
            ],
        ),
        migrations.AddConstraint(
            model_name="workflowedgerun",
            constraint=models.UniqueConstraint(fields=["workflow_run", "edge"], name="unique_workflow_edge_run"),
        ),
        migrations.CreateModel(
            name="HumanTask",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(
                    choices=[("pending", "Pending"), ("submitted", "Submitted")],
                    default="pending",
                    max_length=20,
                )),
                ("form_schema", models.JSONField(default=dict)),
                ("submitted_data", models.JSONField(blank=True, null=True)),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("node_run", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="human_task",
                    to="mandragora.workflownoderun",
                )),
            ],
        ),
    ]
