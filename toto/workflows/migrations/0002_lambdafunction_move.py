import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


def copy_lambdas_to_workflows(apps, schema_editor):
    OldLambdaFunction = apps.get_model("mandragora", "LambdaFunction")
    NewLambdaFunction = apps.get_model("workflows", "LambdaFunction")

    for old in OldLambdaFunction.objects.all():
        NewLambdaFunction.objects.update_or_create(
            id=old.id,
            defaults={
                "function_name": old.function_name,
                "content": old.content,
                "stdout": old.stdout,
                "stderr": old.stderr,
                "created_at": old.created_at,
                "kernel_id": old.kernel_id,
            },
        )


def copy_lambdas_to_mandragora(apps, schema_editor):
    OldLambdaFunction = apps.get_model("mandragora", "LambdaFunction")
    NewLambdaFunction = apps.get_model("workflows", "LambdaFunction")

    for new in NewLambdaFunction.objects.all():
        OldLambdaFunction.objects.update_or_create(
            id=new.id,
            defaults={
                "function_name": new.function_name,
                "content": new.content,
                "stdout": new.stdout,
                "stderr": new.stderr,
                "created_at": new.created_at,
                "kernel_id": new.kernel_id,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("mandragora", "0002_kerneldependency_auto_close"),
        ("workflows", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="LambdaFunction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("function_name", models.CharField(max_length=255, unique=True)),
                ("content", models.TextField(blank=True)),
                ("stdout", models.TextField(blank=True)),
                ("stderr", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("kernel", models.OneToOneField(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="lambda_function",
                    to="mandragora.computekernel",
                )),
            ],
        ),
        migrations.RunPython(copy_lambdas_to_workflows, copy_lambdas_to_mandragora),
        migrations.AlterField(
            model_name="workflownode",
            name="lambda_function",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="workflow_nodes",
                to="workflows.lambdafunction",
            ),
        ),
    ]
